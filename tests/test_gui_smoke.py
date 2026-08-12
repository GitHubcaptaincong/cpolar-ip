import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tkinter as tk

from cpolar_notifier.gui import CpolarNotifierGui


@unittest.skipUnless(os.name == "nt", "Windows Tk/system-tray smoke test")
class GuiSmokeTests(unittest.TestCase):
    def test_hidden_gui_can_restore_and_exit_through_tray(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"CPOLAR_NOTIFIER_HOME": directory}
        ):
            root = tk.Tk()
            root.withdraw()
            gui = CpolarNotifierGui(
                root,
                start_hidden=True,
                start_monitoring=False,
                enable_tray=True,
            )
            try:
                root.update_idletasks()
                self.assertIsNotNone(gui.tray)
                self.assertTrue(gui.tray.supported if gui.tray else False)
                gui._show_window()
                root.update_idletasks()
                self.assertEqual(root.state(), "normal")
            finally:
                gui._exit_application()

    def test_notification_settings_survive_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"CPOLAR_NOTIFIER_HOME": directory}
        ), patch("cpolar_notifier.gui.set_autostart_enabled"):
            root = tk.Tk()
            root.withdraw()
            gui = CpolarNotifierGui(root, enable_tray=False)
            gui.smtp_enabled_var.set(True)
            gui.smtp_host_var.set("smtp.163.com")
            gui.smtp_port_var.set(465)
            gui.smtp_security_var.set("SSL")
            gui.smtp_username_var.set("example@163.com")
            gui.smtp_sender_var.set("example@163.com")
            gui.smtp_recipients_var.set("receiver@qq.com")
            gui.smtp_password_var.set("client-authorization-password")
            self.assertTrue(gui.save_settings(show_confirmation=False))
            gui._exit_application()

            second_root = tk.Tk()
            second_root.withdraw()
            second = CpolarNotifierGui(second_root, enable_tray=False)
            try:
                self.assertTrue(second.smtp_enabled_var.get())
                self.assertEqual(second.smtp_host_var.get(), "smtp.163.com")
                self.assertEqual(second.smtp_username_var.get(), "example@163.com")
                self.assertEqual(
                    second.smtp_password_var.get(), "client-authorization-password"
                )
            finally:
                second._exit_application()

    def test_mail_presets_change_visible_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"CPOLAR_NOTIFIER_HOME": directory}
        ), patch("cpolar_notifier.gui.messagebox.showinfo"), patch(
            "cpolar_notifier.gui.set_autostart_enabled"
        ):
            root = tk.Tk()
            root.withdraw()
            gui = CpolarNotifierGui(root, enable_tray=False)
            try:
                gui._apply_163_preset()
                self.assertTrue(gui.smtp_enabled_var.get())
                self.assertEqual(gui.smtp_host_var.get(), "smtp.163.com")
                self.assertEqual(gui.smtp_port_var.get(), 465)
                self.assertEqual(gui.smtp_security_var.get(), "SSL")
                gui._apply_qq_preset()
                self.assertEqual(gui.smtp_host_var.get(), "smtp.qq.com")
            finally:
                gui._exit_application()


if __name__ == "__main__":
    unittest.main()
