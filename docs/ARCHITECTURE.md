# How Blink Plus works

Blink Plus is a single Home Assistant config-entry integration. This fork pins BlinkPy to `0.25.9`, the version used by Home Assistant 2026.11.2 and reviewed for this change. HA owns credentials, polling, entities, stream players, and media browsing. No separate relay service, Android app, external container, or exposed network port is required.

## Setup and lifecycle

The config flow signs in through Blink, requests MFA when necessary, and stores the resulting authentication session in HA's config entry. Passwords and verification codes are not retained by this integration. Reauthentication preserves the registered hardware ID. The coordinator exchanges the stored refresh token itself at startup and shortly before expiry, so a refused token (reauthentication) is distinguished from a Blink outage (retry); blinkpy's startup would otherwise fall back to a password login that cannot succeed without a stored password. Session data remains sensitive and is protected by HA's storage and backup security.

Wired Floodlight (`superior`) and Mini 2 (`hawk`) cameras also get a light entity when the installed blinkpy provides the lighting call. Commands go through the same serialized control path. State comes from `light_status` in the camera's settings response, read at startup, on each poll, after a command and periodically while the lamp is on; a failed read leaves the last accepted command as an assumed state. Blink answers the REST lights request with code 307 ("System is busy") while a live session is open on the camera, so when the relay holds one the light entity sends the command on that session instead: IMMI message type `0x14` with command 1 (on) or 2 (off) and no payload. The camera reports its lamp on the accessory channel, type `0x15` with 0 (off) or 1 (on) in the sequence field, when the session opens and after each change; the relay passes these to the light entity. Mount positions use the same type with longer payloads and other identifiers. The coordinator polls cloud state, serializes control operations, and refreshes state following commands. Sensors are created only for available data, including capabilities reported later. Diagnostics use an allowlist and omit account identifiers, camera names, credentials, and media URLs.

Unload and HA shutdown close relays and cancel owned timers/downloads. Options updates reload the entry. Local MP4 recordings persist; temporary cloud caches do not.

## Live video path

```mermaid
flowchart LR
  Blink[Blink cloud / camera] -->|Native IMMI stream| Relay[Per-camera loopback relay]
  Relay -->|H.264 + AAC in MPEG-TS| Stream[HA stream integration]
  Stream -->|HLS| Player[HA browser player]
```

Each camera receives an ephemeral TCP listener bound to loopback. The upstream live session starts when a player connects, rather than when HA loads. Viewers of that camera share its upstream. Different cameras have independent relays. Blink's framing is decoded into MPEG-TS without converting every frame to a JPEG.

The relay scopes a compatibility fix to each connection, validates payload lengths, bounds client writes, and cleans up disconnects. Session duration and reconnect cooldown constrain repeated cloud/camera use. HA's per-camera preload setting is forced off for these streams, so a stream with no viewer idles out instead of restarting. Native video preserves the camera's actual resolution and frame rate; HA stream probing and HLS buffering still contribute to startup time.

For DNS-addressed IMMIS relays, the native transport uses Python's default trust store and verifies the relay hostname. For IP addresses returned by Blink's live-view API, it uses a TLS context that trusts only the bundled Blink certificate. It sends the app's `*.immedia-semi.com` service name as SNI and identifies the peer by the SHA-256 fingerprint of the exact leaf certificate, since the certificate does not name the relay by IP. Both paths require TLS certificate verification before the IMMI authentication header is sent. REST requests use HA's aiohttp session and its regular HTTPS trust policy.

The bundled public certificate was extracted from `lib/arm64-v8a/libwalnut.so` in the signed ARM64 split for Blink Home Monitor 62.0 (Android version code 29893937). Android's `apksigner` verified the v3 signature against the app fingerprint Blink publishes in its [Digital Asset Links statement](https://applinks.blink.com/.well-known/assetlinks.json). The relay certificate has SHA-256 fingerprint `b1bfa71ba445f0de14172a1382db19e8848f93b850eb78bd13fdabc1be7e2481`. If Blink rotates the certificate, verify its replacement against a new vendor-signed app before updating the pin.

The relay builds on BlinkPy's live stream object, which is not a stable public interface. Before starting a session the relay checks that the object has the parts it uses. If they are missing, live view reports `incompatible_blinkpy`. The contract tests check that BlinkPy's stream dispatches authentication and reads to the methods replaced on the relay instance. The CI job against the newest BlinkPy release gives early warning before updating the pin.

## Direct live stream

`/api/blink_plus/live/<camera entity id>` serves the relay's MPEG-TS bytes over HTTP to an authenticated Home Assistant API client that may read that entity. It exists for players that need less delay than HLS allows, for example a custom dashboard that drives a pan-tilt mount: HLS adds at least its part hold-back on top of the camera's own delay, while this stream adds none. The view is simply another client of the camera's loopback relay, so it shares the upstream session with Home Assistant's player, is bound by the same session limit and cooldown, and ends the camera session when the last viewer disconnects. Browsers cannot play MPEG-TS directly; a client remuxes it (for example `ffmpeg -c copy` to fragmented MP4 for Media Source Extensions).

## Two-way talk

The live-view API's per-session `liveview_token` must populate the 64-byte token field in the IMMI authentication header. Blinkpy 0.25.9 fills that field with zeros, which can receive video while leaving talk silent when the Blink app is closed. The relay retains the response token and overrides the header on its own stream instance. The token stays in memory. The app-like setup also appends the initial IMMI keepalive (type 0x0a, sequence 0), uses fixed speaker control ID 3 to start and 4 to stop, and waits for a fresh empty type 0x18 reply before sending audio. The complete sequence was audibly verified through the HA endpoint with the app closed. Token insertion alone stayed silent in that endpoint test. Responses without a token retain blinkpy's receive-only behavior.

`/api/blink_plus/talk/<camera entity id>` (POST, authenticated, entity control permission) speaks audio to the camera through an open live session. The body is a stream of ADTS AAC frames (AAC-LC, 16 kHz, mono); the view splits it into frames and writes each to the camera's live connection as an IMMI talk packet (message type `0x05`), the same way pan/tilt commands are written. Live view must be open (the relay session active), exactly as in the Blink app, where talk works only during live view. The camera's own microphone audio arrives the other way, muxed into the native video. A client captures a microphone and transcodes it to this format (for example with ffmpeg); the integration does not re-encode.

The HTTP view serializes talk requests per camera, checks control permission, accepts only AAC-LC 16 kHz mono ADTS frames and limits a request to 60 seconds. It retains the original stream identity so cleanup cannot close a newer session. A timeout, client cancellation or end-of-input closes the original speaker when it still exists. The HTTP interface accepts pre-encoded client audio. See [Two-way talk](TWO_WAY_TALK.md).

## Native dashboard microphone

The bundled custom:blink-plus-ptt element and worklet are served from /blink_plus/frontend. From 1.1.0 `panel.py` registers `blink-plus-camera-card.js` with the frontend as an extra module (version in the address), so the card and the push-to-talk element are available on every dashboard without a Resources entry, and registers a custom sidebar panel (`blink-plus-panel.js`) while any account has the sidebar option on. Both are skipped when the frontend integration is not running. The card owns its `<video>` element: it requests the stream with the `camera/stream` websocket command and plays HLS through the bundled hls.js or the browser's native support; it uses no internal Home Assistant player elements. The worklet emits 100 ms packets of 16 kHz mono PCM. HA's authenticated websocket carries the packets to an in-memory PyAV AAC encoder, then the existing relay sends ADTS frames to the camera.

A microphone session owns its original websocket, entity, live stream and shared per-camera talk lock. Sequence checks and a four-packet queue bound stale input. Five seconds without input or the one-minute session limit ends talk. Releasing drains the bounded final audio before closing the speaker. Disconnect and integration unload cancel the worker and release its lock. A new hold waits for the preceding stop to finish.

See [Native push-to-talk](NATIVE_PTT.md) for the module resource and card configuration.

## Local MP4 recording

The recording button uses HA's camera/stream recording path. It requests a bounded native live session and writes an H.264/AAC MP4 into an allowed HA directory. Duration is checked against the live-session limit. Successful saves update the camera attribute and emit a completion event. This feature does not submit a Blink cloud-record command or modify cloud retention.

## Cloud clips

The media-source provider fetches paginated Blink cloud metadata and groups valid clips by account and camera. History, clip count, and refresh settings bound the catalogue. Deleted or malformed entries are excluded.

A player receives a short-lived signed HA URL, not a Blink token or raw cloud URL. The HTTP view accepts authenticated HA requests or valid HA-signed URLs. It validates entry, media kind, clip identity, and the exact HTTPS Blink origin before fetching media.

Blink's origin ignored Range requests in testing. The integration therefore downloads a clip into a private temporary cache, then lets HA serve byte ranges for seeking. Per-clip locks deduplicate concurrent downloads, a semaphore bounds transfers, and per-file/total size limits bound storage. Failed partial files are removed; eviction and unload clean up cached media. Signed URLs expire after 10 minutes, so treat them as temporary access grants.

## Controls and limitations

Snapshots request a new camera image and wait for updated thumbnail metadata. Ordinary camera stills use the cached image. Motion and arming use Blink control APIs followed by a state refresh. The Motion binary sensors do not rely on the library's per-refresh motion flag alone: at every poll and after every command the coordinator reads Blink's list of recent clips and keeps each camera's newest clip time, and a sensor turns on for two minutes when that time advances. Optional mount commands travel over the native live connection.

This integration does not implement push events, USB clip retrieval, or every setting in the Blink app. Model-specific capabilities and cloud API behavior can change independently of this project. See [validation evidence](VALIDATION.md) for what was actually tested.
