"""Live smoke test for evidence clients. Requires internet. Not a unit test."""
from legacylens.core.config import Settings
from legacylens.evidence.eol import EndOfLifeClient
from legacylens.evidence.osv import OsvClient
from legacylens.parsing.manifests.models import Ecosystem

cfg = Settings(_env_file=None)
cycles = EndOfLifeClient(cfg).get_cycles("spring-framework")
eol = [c for c in cycles if c.is_eol()]
print(f"spring-framework: {len(cycles)} cycles, {len(eol)} EOL; e.g. {eol[0].cycle} EOL {eol[0].eol_date}")

vulns = OsvClient(cfg).query("flask", Ecosystem.PYPI, "0.12.4")
print(f"flask 0.12.4: {len(vulns)} known vulnerabilities; e.g. {vulns[0].id}: {vulns[0].summary}")
