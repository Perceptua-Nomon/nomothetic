# Raspberry Pi Hardware Reference

Hardware discovery notes and register maps for the nomon fleet devices.

---

## Hardware

**Device:** Raspberry Pi Zero 2 W running Debian GNU/Linux 13 (trixie)  
**Camera:** OV5647 (Pi Camera v1.3) via FPC ribbon cable  
**HAT:** SunFounder Robot HAT V4


## Robot HAT V4 Hardware Discovery

### I2C Bus Scan

The Robot HAT V4 controller is found on **I2C bus 1, address 0x14**:

```bash
sudo i2cdetect -y 1
```

```
     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
00:                         -- -- -- -- -- -- -- -- --
10: -- -- -- -- 14 -- -- -- -- -- -- -- -- -- -- --
```

I2C buses 10 and 11 (muxed) show `0x36` as `UU` (in-use by kernel driver) —
these are the OV5647 camera sensor buses and should not be touched.

SPI nodes `/dev/spidev0.0` and `/dev/spidev0.1` exist, but the Robot HAT V4
is primarily an I2C device. SPI is available for future expansion.

### Robot HAT V4 Register Map

Derived from
[sunfounder/robot-hat commit d856cfb](https://github.com/sunfounder/robot-hat/commit/d856cfb67f06e69150bbbb58e750f1db3097c39d):

| Constant | Value | Purpose |
|----------|-------|---------|
| `REG_CHN` | `0x20` | PWM channel base register |
| `REG_PSC` | `0x40` | Prescaler (PWM group 1) |
| `REG_ARR` | `0x44` | Auto-reload / period (group 1) |
| `REG_PSC2` | `0x50` | Prescaler (PWM group 2) |
| `REG_ARR2` | `0x54` | Auto-reload / period (group 2) |
| `CLOCK_HZ` | 72 MHz | PWM controller clock |
| `PERIOD` | 4095 | Servo PWM period ticks |
| `SERVO_FREQ` | 50 Hz | Standard servo frequency |

Servo pulse width range: **500–2500 µs** (0°–180°).

Battery ADC: channel **A4**, command `(7 - 4) | 0x10 = 0x13`, scaling
`battery_v = adc_voltage × 3`.

Named GPIO pins:

| HAT Name | BCM | Direction |
|----------|-----|-----------|
| `D4` | 23 | Output |
| `D5` | 24 | Output |
| `MCURST` | 5 | Output (MCU reset) |
| `SW` | 19 | Input |
| `LED` | 26 | Output |

MCU reset procedure: assert BCM5 low for ≥ 10 ms, then high.

All hardware control is implemented in the **nomopractic Rust daemon**.
The Python `nomothetic` package communicates with it via IPC only — it does not
write I2C registers or toggle GPIO directly. See
[hat_ipc_schema.md](hat_ipc_schema.md).

---

## Software Setup

For instructions on installing and running the nomothetic API server and the
nomopractic daemon, see [pi_setup.md](pi_setup.md).