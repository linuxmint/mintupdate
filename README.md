Related issue: #1025
Post-update check for kernel updates on UEFI dual-boot systems with instructions
Adds a post-update check after kernel updates to detect potential bootloader
issues on UEFI dual-boot systems (Linux Mint + Windows) and provides the user
with instructions to recover safely.

Motivation: Prevents unbootable system scenarios similar to reported incidents.

Changes:
- Added `post_kernel_update_check()` called after kernel updates
- Detects UEFI + dual-boot with Windows
- Shows dialog with recovery instructions if potential issue detected
