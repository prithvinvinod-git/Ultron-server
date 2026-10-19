# ESP32 touchscreen UI firmware (T531)

Firmware skeleton for the ESP32-WROOM-32 + 2.4-inch touchscreen companion UI.

> **Status: skeleton, not compiled, not verified.** No toolchain and no hardware
> are available in this repository, and the hardware specification is still
> unknown (`docs/esp32-hardware-t529.md`). Per §69.9 and D045, this code is
> written-but-unverified: it is not claimed to compile or run.
>
> `firmware/esp32-ui/hardware_config.h` intentionally fails to compile until the
> T529 unknowns (display controller, resolution, touch controller, pin map) are
> filled in. That is deliberate: no pin or controller is assumed.

## Layout (per §69.9)

```
firmware/esp32-ui/
  esp32-ui.ino          main sketch: setup/loop, non-blocking scheduler
  hardware_config.h     pins, controller, calibration (T529 unknowns -> #error)
  display_manager.*     display init + partial-region redraws (no full framebuffer)
  touch_manager.*       touch read + calibration
  screen_manager.*      current-screen state machine + touch navigation
  screens/              one file per screen (T532)
  network_manager.*     WiFi + connection state, non-blocking reconnect
  ultron_client.*       ULTRON protocol client (T525 subset)
```

## Design rules

- **Thin client.** The device never runs AI models and never renders the desktop
  UI (§69.9, D044). It speaks the ESP32 protocol subset (T525).
- **Non-blocking.** The UI never blocks on the network; redraws are partial
  region pushes, with no full framebuffer allocation (§69.9).
- **Reuse, do not duplicate.** The protocol client is a strict subset of the
  server device contract; the server remains authoritative (T525).

## Build (when a toolchain exists)

Not run here. Intended path: Arduino-ESP32 core (or PlatformIO) with
`TFT_eSPI` configured via `hardware_config.h` → `User_Setup.h`, plus the touch
driver chosen in `docs/esp32-libraries-t530.md`. Compilation is recorded under
T534.

## Verification status

| Task | Status |
|---|---|
| T531 skeleton | written, **unverified** (no toolchain) |
| T532 screens | written, **unverified** (no display) |
| T533 touch/nav/reconnect | written, **unverified** (no touch, no network run) |
| T534 compile | **unverified** (no toolchain) |
