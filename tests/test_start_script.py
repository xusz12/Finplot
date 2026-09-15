import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "start-finplot.sh"


class StartScriptTests(unittest.TestCase):
    def run_script(self, tailscale_output: str | None, mode: str, **extra_env):
        with tempfile.TemporaryDirectory() as directory:
            bin_dir = Path(directory)
            if tailscale_output is not None:
                executable = bin_dir / "tailscale"
                executable.write_text(
                    "#!/bin/sh\n"
                    "[ \"$1 $2\" = \"ip -4\" ] || exit 1\n"
                    "printf '%b' \"$FAKE_TAILSCALE_OUTPUT\"\n"
                )
                executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
            env = {
                **os.environ,
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "LEDGER_DB": "/tmp/isolated-test-ledger.sqlite3",
                "FINPLOT_DRY_RUN": "1",
                "FAKE_TAILSCALE_OUTPUT": tailscale_output or "",
                **extra_env,
            }
            return subprocess.run(
                [str(SCRIPT), mode], cwd=ROOT, env=env,
                text=True, capture_output=True, check=False,
            )

    def test_tailscale_modes_use_discovered_address_and_profile_ports(self):
        beta = self.run_script("100.86.236.103", "tailscale-beta")
        stable = self.run_script("100.86.236.103", "tailscale-stable")
        override = self.run_script("100.86.236.103", "tailscale-beta", FINPLOT_PORT="9001")
        self.assertEqual((beta.returncode, beta.stdout), (0, "bind_host=100.86.236.103\nport=8775\n"))
        self.assertEqual((stable.returncode, stable.stdout), (0, "bind_host=100.86.236.103\nport=8766\n"))
        self.assertEqual((override.returncode, override.stdout), (0, "bind_host=100.86.236.103\nport=9001\n"))

    def test_tailscale_mode_fails_closed_for_missing_or_invalid_address(self):
        for output in (None, "", "192.168.1.8", "0.0.0.0", "100.86.236.103\n100.86.236.104", "not-an-ip"):
            with self.subTest(output=output):
                result = self.run_script(output, "tailscale-beta")
                self.assertEqual(result.returncode, 2)

    def test_tailscale_mode_rejects_invalid_ports(self):
        for port in ("0", "65536", "-1", "abc", ""):
            with self.subTest(port=port):
                result = self.run_script("100.86.236.103", "tailscale-beta", FINPLOT_PORT=port)
                self.assertEqual(result.returncode, 2)

    def test_loopback_default_remains_available(self):
        result = self.run_script(None, "loopback")
        self.assertEqual((result.returncode, result.stdout), (0, "bind_host=127.0.0.1\nport=8766\n"))


if __name__ == "__main__":
    unittest.main()
