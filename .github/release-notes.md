## Which file do I download?

| You have | Download | Then |
| --- | --- | --- |
| **Windows 10 / 11** | `datapipe-windows-setup.exe` | Double-click it and approve the administrator prompt. It installs for everyone on the computer, into `Program Files`, where Windows protects it from being changed by programs that run without administrator rights. Start it from the Start menu. |
| Windows, no administrator rights | `datapipe-windows-setup.exe` | Run it from a terminal as `datapipe-windows-setup.exe /CURRENTUSER`: it installs for you only. Or use `datapipe-windows.exe`, one file, nothing installed. |
| **Mac with an Apple chip** (M1, M2, M3 ...) | `datapipe-macos-arm64.dmg` | Open it, drag `datapipe` to Applications. The first time, macOS asks before opening it: see the note inside the disk image. |
| **Mac with an Intel chip** | `datapipe-macos-x86_64.dmg` | The same. ( > About This Mac tells you which chip you have.) |
| Linux | `datapipe-linux-x86_64.tar.gz` | `tar xzf datapipe-linux-x86_64.tar.gz && ./datapipe` |

datapipe opens in your web browser and keeps everything on your computer: nothing is sent anywhere. Your data files go in `datapipe/files` in your home folder, the results are written to `datapipe/work`.

## Is it safe? What to expect

- **These files are not code-signed.** A signature needs a certificate that somebody pays for (Windows) or an Apple Developer ID (macOS). So Windows says "Unknown publisher" and macOS says it cannot check the app for malware. The source code is public in this repository and every file here is built from it by GitHub's own build, which you can read under *Actions*.
- **Check your download** against `SHA256SUMS.txt` (Windows: `Get-FileHash <file>`, Mac/Linux: `shasum -a 256 <file>`). The numbers must be identical.
- Windows Security (Smart App Control, SmartScreen) may still refuse an unsigned program on a computer where it is strict. That is the missing signature, not something wrong with the file.

Full instructions: <https://github.com/Aleks108-heaven/datapipe-prototype/blob/main/docs/INSTALL.md>
