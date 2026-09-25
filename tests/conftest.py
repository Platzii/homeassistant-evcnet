"""Load the integration package without running its HA setup module."""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("custom_components")
package.__path__ = [str(ROOT / "custom_components")]
sys.modules.setdefault("custom_components", package)
package = types.ModuleType("custom_components.evcnet")
package.__path__ = [str(ROOT / "custom_components/evcnet")]
sys.modules.setdefault("custom_components.evcnet", package)
