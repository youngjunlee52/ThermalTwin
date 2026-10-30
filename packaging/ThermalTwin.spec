# PyInstaller 설정 — ThermalTwin.exe 배포판 (폴더형: ThermalTwin\ThermalTwin.exe + _internal\)
# 빌드: build_exe.bat  (또는 backend\.venv\Scripts\python.exe -m PyInstaller packaging\ThermalTwin.spec)
# 주의: 서명 없는 exe 는 '스마트 앱 컨트롤'이 켜진 PC 에서 실행이 차단됩니다 (코드 서명 필요).
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "backend" / "models"), "backend/models"),
    (str(ROOT / "data" / "real_ev"), "data/real_ev"),
    (str(ROOT / "frontend" / "dist"), "frontend/dist"),
    (str(ROOT / "tools" / "thermaltwin_bms.dbc"), "tools"),
]
datas += collect_data_files("cantools")

hiddenimports = [
    "main", "can_gateway", "convert_changan",
    *collect_submodules("uvicorn"),
    *collect_submodules("can.interfaces"),
    *collect_submodules("cantools"),
    "websockets.legacy", "websockets.asyncio.server", "websockets.asyncio.client",
]

a = Analysis(
    [str(ROOT / "backend" / "launcher.py")],
    pathex=[str(ROOT / "backend"), str(ROOT / "tools")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "PyInstaller"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ThermalTwin",
    console=True,           # 실행 중 안내와 종료(창 닫기)를 위해 콘솔 창을 둠
    upx=False,              # UPX 압축은 백신 오탐을 늘려서 끔
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="ThermalTwin")
