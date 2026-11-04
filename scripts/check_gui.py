"""Exercise actual GUI construction without opening a visible window."""
import fmpy
import fmpy.scenario
from PySide6.QtWidgets import QApplication
from fmpy.gui.MainWindow import MainWindow

app = QApplication(["setup-check", "-platform", "offscreen"])
window = MainWindow()
from fmpy.gui.scenario import ScenarioDialog
dialog = ScenarioDialog(parent=window)
dialog.close()
app.processEvents()
window.close()
app.processEvents()
print(f"Ready: FMPy {fmpy.__version__}; MainWindow constructed successfully")
print(fmpy.__file__)
