"""Require the running X1P CAMSS driver's capability, never a uname guess."""
from pathlib import Path

CAPABILITY = 'x1p-normal-world-v1'


def require_camss(root=Path('/sys/bus/platform/drivers/qcom-camss')):
    for path in root.glob('*/facelock_capability'):
        try:
            if path.read_text().strip() == CAPABILITY:
                return
        except OSError:
            pass
    raise RuntimeError('A14 requires the CAMSS normal-world mapping patch and '
                       'capability marker in the running kernel; install the patched kernel '
                       'and reboot (see patches/README.md)')
