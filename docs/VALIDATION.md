# Blink Plus validation - 2026-11-10

## 1.1.3 motion sensor validation

The integration suite passes 139 automated tests, with five live groups skipped in normal runs. New tests cover picking each camera's newest clip from Blink's list (bad, deleted and other cameras' rows ignored), a clip listed before startup not counting as motion, a newer clip turning the sensor on without the library flag, the two-minute hold, an older clip turning up later, a failed clip request leaving the poll working, the library flag still counting, the check after a command, and the pending release being cancelled on unload.

The fault was measured on the owner's installation with 1.1.2, from Home Assistant's history of 2026-11-10 to 2026-11-17 for seven cameras: of 463 clips shown by the Last recording sensors, 71 coincided with the camera's Motion sensor turning on. One battery camera recorded 13 clips and its Motion sensor never turned on. That installation takes snapshots on a timer, which is what cleared the library flag.

Live check on the owner's installation, 2026-11-17: with the 1.1.3 files installed and Home Assistant restarted, three cameras recorded clips within the next six minutes (a battery camera, a second battery camera and the doorbell). All three Motion sensors turned on at the following poll, including the camera whose sensor had not turned on for a week.

Limits: one live check of three clips. The share of clips reported over a longer period was not measured.

## 1.1.2 light during live view validation

The integration suite passes 131 automated tests, with five live groups skipped in normal runs. New tests cover lamp reports on the accessory channel (with and without payload, not confused with mount positions), the exact on and off packets, waiting for the camera's confirmation, the command being refused without an open session, the light entity choosing the live session over the REST request while one is open, instant state from a camera report, and a failed session command raising a fixed message.

Live checks on the owner's cameras, 2026-11-16:

- With a Blink Plus live view open, Blink answered the REST lights request with code 307, "System is busy, please wait", for both on and off. The dashboard card showed that as "Blink is busy".
- Mini 2: with a live session open, the on command on the session was confirmed by the camera in about 0.1 seconds and Blink's reported `light_status` read on; the off command likewise read off.
- Wired Floodlight: the same sequence gave the same results (confirmations in about 0.12 seconds).
- Both checks ran through the integration's own relay class, started the way a viewer starts it.

Limits: these checks ran from the development mirror, not from the dashboard button on an installed 1.1.2. The lamps were not watched in person. A lamp switched by motion during a live view was not exercised. The command format matches what another open-source project documents for the Blink app; it is not published by Blink and may change.

## 1.1.0 sidebar page, camera card and automatic loading validation

The integration suite passes 127 automated tests, with five live groups skipped in normal runs. New tests cover: nothing registered when the frontend is absent; the card script registered with its version; the sidebar page added with an account, removed with it, and following the option; the option offered and saved; and the shipped files (card, page, hls.js) being wired together without any reference to Home Assistant's internal camera player. CI syntax-checks the card and page scripts.

Browser check on the owner's Home Assistant 2026.11.1 (Chrome, desktop), 2026-11-16, with the card and page scripts served by the integration:

- Card on a Mini 2: 1920x1080 live video played in the card's own player through hls.js about 1.2 seconds behind live, with the browser's controls, Hold to talk and the Spotlight button on the picture.
- Page: all seven cameras listed with names, New picture / Record clip / Motion detection buttons and readings, the system's arm state in the header, and no live video until **Start live view** was pressed. Pressing it on a Mini with a pan/tilt mount played video about 1.4 seconds behind live and then showed Hold to talk and the pan/tilt pad; no other camera started.

Limits: in that check the scripts were loaded by hand into an open Home Assistant page; automatic loading and the sidebar entry are covered by the automated tests and were first exercised on a real installation after release. The buttons on the card were not pressed in the browser (the same actions are tested through their entities). Phones, the companion apps, Safari's native HLS path and more than one account were not checked.

## 1.0.2 reported light state and Mini 2 spotlight validation

The integration suite passes 121 automated tests, with five live groups skipped in normal runs. Light tests cover discovery for both models, reported state at startup, commands, busy and rejected answers, following a lamp that switches itself off, a lamp switched on outside Home Assistant, the limit on follow-up reads, failed or empty status reads (state kept and marked assumed, no exception text logged) and a blinkpy without the lighting call.

Live checks on the owner's account, 2026-11-16:

- Wired Floodlight: `light.turn_on` and `light.turn_off` through Home Assistant 1.0.1 were accepted, and Blink's camera settings reported `light_status` on within about 3 seconds and off within about 3 seconds.
- Wired Floodlight left on: Blink reported it off again about two and a half minutes later with no command sent, while the 1.0.1 entity still showed on. This is the gap 1.0.2 closes.
- Mini 2: the same command was accepted (Blink names it `accessory_lights_on`) and `light_status` reported on within about 4 seconds and off after the off command.

Limits: the lamps were not watched in person during these checks, so the evidence is Blink's own report. Motion-triggered lighting and switching from the Blink app were not exercised. The Mini 2 check used direct API calls, before the Spotlight entity existed.

## 1.0.1 login renewal and floodlight validation

The integration suite passes 113 automated tests, with five live groups skipped in normal runs.

Login renewal: tests cover a successful renewal (new tokens saved, password never sent or stored), a response without a new refresh token, refused tokens (HTTP 400 and 401), and outages (HTTP 429, 500 and 503, malformed bodies, connection errors and timeouts). Real Home Assistant lifecycle tests confirm that an outage during startup leaves the entry retrying with no reauthentication flow, that a refused token starts exactly one reauthentication flow, that a token near expiry is renewed before the poll, that an outage while running recovers on a later poll, and that entries without a refresh token keep the previous startup path. A compatibility test checks that the installed blinkpy still provides the constants and setup steps this relies on.

The weakness was found by reading blinkpy 0.25.9 and was not reproduced against Blink's service; a real outage cannot be produced on demand. The renewal request is the same one blinkpy sends.

Floodlight: tests cover entity discovery by product type, the on/off services, busy and rejected answers and a blinkpy without the lighting call. No physical Wired Floodlight was operated before release.

## 0.3.0 native HA push-to-talk validation

The integration suite passes 86 automated tests, including native PCM-to-AAC decoding, websocket session ownership, shared HTTP/microphone locking, microphone disconnect cleanup and three repeated start/audio/stop sequences. JavaScript syntax checks run in CI.

An actual HA picture-elements live view loaded the bundled microphone element. A browser with a synthetic microphone transmitted 28 audio packets during a three-second hold. All commands succeeded, release ended the microphone track and completed one stop command. At 390 px phone width, the microphone control fit between x=24 and x=155 while the existing pan/tilt pad remained accessible.

The owner confirmed real voice on the native HA button and reported that a second hold failed. The repeated-hold regression reproduced the increasing control-ID defect. The correction uses fixed start/stop IDs and waits for close completion before accepting another hold. After the correction, the owner verified seven successful holds in the same live view without reopening it.

## 0.2.0 talk validation

The 0.2.0 release adds independent-session talk support and API safeguards. The integration suite passes 83 automated tests, with five live groups skipped in normal runs. Official release CI is recorded in GitHub Actions.

A Mini-class camera played synthetic AAC tones and the owner's actual voice through a custom browser dashboard. The final successful voice test used the production HA talk endpoint with the official Blink app closed. Preserving only the media token was insufficient in an earlier endpoint test. The validated sequence also sends the initial keepalive, uses the talk-control sequence and waits for a fresh reply.

The separate dashboard's browser test sent 29 microphone packets in roughly three seconds, received successful start/data/stop responses and verified microphone tracks ended on release. Isolated client UI checks verified permission-denied feedback and close-during-talk cleanup. The complete dashboard suite passed 293 tests, followed by 64 affected checks after small cleanup changes. Those client checks belong to the separate dashboard and are additional to the integration's 83 tests.

Talk speaker playback was verified on one Mini-class camera. Other camera speakers, actual wall-panel microphone hardware, phone/browser combinations, casting and long-term reliability remain open test areas. The microphone UI is supplied by a compatible client. See [Two-way talk](TWO_WAY_TALK.md).

## Historical 0.1.0 viewing, recording and cloud validation

The following results document the original 0.1.0 validation run. The original production-state statements describe that historical run. The owner subsequently installed Blink Plus and verified the 0.2.0 talk development build in production.

Test environment: isolated Home Assistant 2026.11.1 / Python 3.14 on the Pi. Production HA independently reports the same Core version. Browser: headless Edge 157 on a separate Windows host, connected to the isolated loopback-only HTTP test server through an SSH tunnel. Production HA was not restarted or modified.

## Final browser results

Each sample measured 20 seconds from the first displayed frame. AAC audio decoded on every camera. No buffering/waiting events were observed in this final run.

| Camera | Native resolution | Displayed fps | Browser startup | Dropped frames |
|---|---|---:|---:|---:|
| Mini A | 1920x1080 | 19.94 | 10.79 s | 2/405 |
| Battery A | 1920x1080 | 29.89 | 14.09 s | 1/603 |
| Doorbell | 1440x1440 | 29.95 | 13.18 s | 1/604 |
| Mini B | 1920x1080 | 14.99 | 10.19 s | 0/304 |
| Battery B | 1920x1080 | 29.88 | 11.28 s | 1/603 |
| Battery C | 1280x720 | 24.00 | 11.49 s | 0/484 |
| Battery D | 1920x1080 | 29.88 | 14.09 s | 2/604 |

Maximum frame gap was 116.7 ms. Final browser suite: 7 passed, clean teardown. Native frame rate varies with camera configuration/conditions; earlier Mini A samples differed. This is a short validation sample, not a long-term reliability claim.

## Real controls and sensor capabilities

- Fresh snapshots: Mini A 17.90 s; Battery A 22.02 s; Doorbell 21.06 s; Mini B 18.96 s; Battery B 19.19 s; Battery C 22.47 s; Battery D 20.93 s. JPEG data and changed thumbnail metadata were verified through HA button services. One earlier snapshot attempt failed transiently before any arming/motion changes; the repeat and all final per-camera checks passed.
- Mini A motion detection toggled to the opposite state, verified from refreshed cloud data, then restored to OFF.
- Network arming toggled, verified, and restored to ARMED.
- Mini B mount acknowledged left/right/up/down/home. Its exact original position was restored and acknowledged.
- A final independent session confirmed the network was armed and Mini A motion detection was off.
- Outdoor cameras reported real Fahrenheit temperature, calibrated temperature, hundredths-of-volt battery values, low/OK state and Wi-Fi dBm. The doorbell reported battery bars and sync bars. The Minis omitted unsupported readings. The HA test environment also verified automatic temperature-unit conversion and late sensor discovery.

## Local recordings

HA's actual record button produced native H.264/AAC MP4s. Both streams were decoded from every saved file.

| Camera class | Requested duration | File duration | File size | Video/audio frames decoded |
|---|---:|---:|---:|---:|
| Mini A Mini | 15 s | 16.237 s | 1,469,690 bytes | 240 / 250 |
| Battery A battery | 15 s | 15.524 s | 2,897,590 bytes | 464 / 240 |
| Doorbell doorbell | 15 s | 14.567 s | 1,952,741 bytes | 436 / 225 |

The three test recordings remain on the Pi under the isolated development workspace's `.validation/recordings` directory. They were not copied into OneDrive, published, or installed into production HA.

## Cloud listing and retrieval

The existing authenticated Blink media API works; phone instrumentation was not needed. The integration's real HA media-source test:

- Browsed 1,000 clips (the configured limit), grouped into seven camera folders.
- Resolved a playable signed HA URL without exposing a Blink URL or token to the player.
- Rejected unsigned access with HTTP 401.
- Downloaded an actual 4,763,759-byte H.264/AAC MP4 (28.42 seconds) into the private temporary cache.
- Served a signed `bytes=0-4095` request as HTTP 206 with 4,096 bytes, even though Blink's origin ignored a direct Range request.
- Retrieved a 32,334-byte JPEG thumbnail and removed the cloud cache on unload.

Cloud pagination, stale/deleted filtering, origin restrictions, duplicate-download caching, configured size bounds, signed URLs, anonymous rejection, range responses and cancellation/unload behavior have automated coverage.

## Automated checks and fixes

58 automated tests pass; five explicitly opted-in live-test groups are skipped during normal runs. Lint checks for Python correctness pass, source/tests were formatted, and the official HA hassfest validator reports zero invalid integrations. Official GitHub HACS and hassfest checks and test results are available in the repository Actions history. Catalog acceptance is separate from automated validation.

Fixed during validation: Configure while unloaded; reauthentication after a failed control refresh; backend option validation; local recording with allowed-directory and session-duration guards; private cloud clip browsing/proxy/cache; and download cancellation/cleanup.

An experimental shortened stream-probing mode was rejected: it saved little startup time but removed Mini B audio and caused a multi-second pause. It is absent from the final build. Standard probing restored audio and smooth playback. Startup remains 10-14 seconds in the final browser samples.

## Limits of the evidence

Fresh credential/MFA entry was simulated in flow tests; actual live tests authenticated with the existing saved Blink session. Settings form logic/schema was tested, not visually exercised in the HA frontend. Audio decoding was verified; a human did not listen to the output. Phone UX, casting, long-term operation, production installation/migration, unsupported camera models, Sync Module USB media, push events and two-way talk are outside this completed validation run.

The original HA integration, dashboards and Pi blink-live service remain unchanged. Release packaging does not change that production-installation status.
