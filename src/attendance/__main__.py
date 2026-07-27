"""Enable `python -m attendance ...`.

The generated attendance.exe shim can be blocked by Windows Application Control
(os error 4551); invoking the module through Python sidesteps that entirely.
"""

from attendance.cli import main

main()
