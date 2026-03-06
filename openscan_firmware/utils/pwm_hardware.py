import subprocess
from pathlib import Path

from dataclasses import dataclass

import atexit
import signal
import sys

@dataclass
class _HwPWM:

    _PWMCHIP = Path("/sys/class/pwm/pwmchip0")

    _PIN_INFO = {
        12: {"channel": 0, "alt": "a0"},
        18: {"channel": 0, "alt": "a5"},
        13: {"channel": 1, "alt": "a0"},
        19: {"channel": 1, "alt": "a5"},
    }

    _pins = {}

    # register cleanup at exit
    def __init__(self):
        atexit.register(_HwPWM._cleanup)
        signal.signal(signal.SIGTERM, _HwPWM._signal_handler)
        signal.signal(signal.SIGINT, _HwPWM._signal_handler)    

    @staticmethod
    def _run(cmd):
        result = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
        try:
            return result.split(":", 1)[1].split()[0]
        except:
            return ""

    @staticmethod
    def _pwm_path(channel):
        return _HwPWM._PWMCHIP / f"pwm{channel}"


    @staticmethod
    def _write(path, value):
        path.write_text(str(value))


    @staticmethod
    def _export(channel):
        p = _HwPWM._pwm_path(channel)
        if not p.exists():
            (_HwPWM._PWMCHIP / "export").write_text(str(channel))


    @staticmethod
    def _unexport(channel):
        p = _HwPWM._pwm_path(channel)
        if p.exists():
            (_HwPWM._PWMCHIP / "unexport").write_text(str(channel))


    @staticmethod
    def supports(pin: int):
        # first check if pin is a supported one
        if not pin in _HwPWM._PIN_INFO:
            return False
        
        # then check if its PWM is not already in use
        chan = _HwPWM._PIN_INFO[pin]["channel"]
        for p in _HwPWM._pins.keys():
            # harmless re-set already set pin
            if p == pin:
                return True
            # if using same channel as already setup pin don't accept it
            if chan == _HwPWM._PIN_INFO[p]["channel"]:
                return False
            
        # available PWM pin and channel not used, ok
        return True

    @staticmethod
    def setup(pin: int):
        if not _HwPWM.supports(pin):
            raise ValueError("unsupported pin or pwm channel in use")

        info = _HwPWM._PIN_INFO[pin]
        ch = info["channel"]

        # configure pin mux
        old_func = _HwPWM._run(["pinctrl", str(pin)])
        _HwPWM._run(["pinctrl", str(pin), info["alt"]])

        # enable pwm channel
        _HwPWM._export(ch)

        pwm = _HwPWM._pwm_path(ch)

        # ensure disabled before configuration
        try:
            _HwPWM._write(pwm / "enable", 0)
        except:
            pass
        
        _HwPWM._pins[pin] = { "freq": 20000.0, "duty": 1.0, "oldfunc": old_func }


    @staticmethod
    def release(pin: int):
        if not pin in _HwPWM._pins:
            return

        info = _HwPWM._PIN_INFO[pin]
        ch = info["channel"]

        pwm = _HwPWM._pwm_path(ch)

        if pwm.exists():
            try:
                _HwPWM.write(pwm / "enable", 0)
            except:
                pass

        # return pin to input
        _HwPWM._run(["pinctrl", str(pin), _HwPWM._pins[pin]["oldfunc"]])
        
        del _HwPWM._pins[pin]

    @staticmethod
    def _set_freq_duty(pin: int, freq: float, duty: float):

        info = _HwPWM._PIN_INFO[pin]
        ch = info["channel"]

        pwm = _HwPWM._pwm_path(ch)

        period_ns = int(1_000_000_000 / freq)
        duty_val = int(period_ns * duty)

        _HwPWM._write(pwm / "enable", 0)
        _HwPWM._write(pwm / "period", period_ns)
        _HwPWM._write(pwm / "duty_cycle", duty_val)
        _HwPWM._write(pwm / "enable", 1)
       
        _HwPWM._pins[pin]["freq"] = freq
        _HwPWM._pins[pin]["duty"] = duty

    @staticmethod
    def set_frequency(pin: int, freq: float):
        if not pin in _HwPWM._pins:
            raise ValueError("pwm pin not initialized")

        info = _HwPWM._PIN_INFO[pin]
        ch = info["channel"]

        duty = _HwPWM._pins[pin]["duty"]
        _HwPWM._set_freq_duty(pin, freq, duty)

    @staticmethod
    def get_frequency(pin: int):
        if not pin in _HwPWM._pins:
            raise ValueError("pwm pin not initialized")

        return _HwPWM._pins[pin]["freq"]

    @staticmethod
    def set_duty_cycle(pin: int, duty: float):
        if not pin in _HwPWM._pins:
            raise ValueError("pwm pin not initialized")

        info = _HwPWM._PIN_INFO[pin]
        ch = info["channel"]

        freq = _HwPWM._pins[pin]["freq"]
        _HwPWM._set_freq_duty(pin, freq, duty)


    @staticmethod
    def get_duty_cycle(pin: int):
        if not pin in _HwPWM._pins:
            raise ValueError("pwm pin not initialized")

        return _HwPWM._pins[pin]["duty"]

    # cleanup routines -- resets PWM pins

    @staticmethod
    def _cleanup():
        to_clean = []
        for pin in _HwPWM._pins.keys():
            to_clean.append(pin)
        for pin in to_clean:
            _HwPWM.release(pin)

    def _signal_handler(signum, frame):
        _HwPWM._cleanup()


# ==========================================================
# SINGLETON
# ==========================================================

# hardware pw, singleton
hwpwm = _HwPWM()
