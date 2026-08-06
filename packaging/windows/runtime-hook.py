import os
import sys
from pathlib import Path

bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))

typelib_dir = bundle_root / "typelibs"
if typelib_dir.is_dir():
    os.environ["GI_TYPELIB_PATH"] = str(typelib_dir)

schema_dir = bundle_root / "schemas"
if (schema_dir / "gschemas.compiled").is_file():
    os.environ["GSETTINGS_SCHEMA_DIR"] = str(schema_dir)

plugin_dir = bundle_root / "gstreamer-1.0"
if plugin_dir.is_dir():
    os.environ["GST_PLUGIN_PATH_1_0"] = str(plugin_dir)
    os.environ["GST_PLUGIN_PATH"] = str(plugin_dir)
    os.environ["GST_PLUGIN_SYSTEM_PATH_1_0"] = ""

    scanner = plugin_dir / "gst-plugin-scanner.exe"
    if scanner.is_file():
        os.environ["GST_PLUGIN_SCANNER"] = str(scanner)