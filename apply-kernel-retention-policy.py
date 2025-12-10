#!/usr/bin/python3
"""
Kernel Package Auto-mark Script

This script analyzes installed kernel packages and ensures they are marked
correctly according to the retention policy.

Rule: Keep at most 2 kernel versions per series (major.minor):
  - The most recent version in each series (kept as manual)
  - The second-most recent version in each series (kept as manual)
  - All older versions are marked auto-installed

Packages within the retention policy are marked as manually-installed.
Packages outside the retention policy are marked as auto-installed.

Kernels from different series (e.g., 6.8.x vs 6.14.x) are treated independently.
The running kernel is always preserved regardless.
"""

import os
import re
import sys
from collections import defaultdict

try:
    import apt
except ImportError:
    print("ERROR: python3-apt is required. Install with: sudo apt install python3-apt")
    sys.exit(1)


class KernelVersion:
    """Parse and compare kernel version strings."""

    def __init__(self, version_string):
        """
        Parse version like "6.8.0-88" into comparable components.
        version_string: e.g., "6.8.0-88" from package linux-image-6.8.0-88-generic
        """
        self.original = version_string
        parts = version_string.replace("-", ".").split(".")

        # Extract numeric components
        self.major = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
        self.minor = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        self.patch = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        self.abi = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0

        # Series is major.minor (e.g., "6.8" or "6.14")
        self.series = f"{self.major}.{self.minor}"

    def __lt__(self, other):
        """Compare versions for sorting."""
        return (self.major, self.minor, self.patch, self.abi) < \
               (other.major, other.minor, other.patch, other.abi)

    def __eq__(self, other):
        """Check equality."""
        return (self.major, self.minor, self.patch, self.abi) == \
               (other.major, other.minor, other.patch, other.abi)

    def __repr__(self):
        return f"KernelVersion({self.original})"

    def __str__(self):
        return self.original


class KernelPackageGroup:
    """Group of packages belonging to one kernel version."""

    def __init__(self, version):
        self.version = version
        self.packages = []  # List of (package_name, is_manual) tuples
        self.is_running = False

    def add_package(self, pkg_name, is_manual):
        """Add a package to this kernel group."""
        self.packages.append((pkg_name, is_manual))

    def __repr__(self):
        manual_count = sum(1 for _, is_manual in self.packages if is_manual)
        auto_count = len(self.packages) - manual_count
        running_flag = " [RUNNING]" if self.is_running else ""
        return f"{self.version.original}: {len(self.packages)} packages " \
               f"(M:{manual_count}, A:{auto_count}){running_flag}"


def get_running_kernel_version():
    """Get the currently running kernel version."""
    try:
        uname_release = os.uname().release  # e.g., "6.8.0-88-generic"
        # Extract version portion (remove kernel type suffix)
        match = re.match(r'^(\d+\.\d+\.\d+-\d+)', uname_release)
        if match:
            return match.group(1)
    except Exception as e:
        print(f"Warning: Could not determine running kernel: {e}")
    return None


def find_kernel_packages():
    """
    Find all installed kernel packages and group them by version.
    Returns dict: {series: [KernelPackageGroup, ...]}
    """
    cache = apt.Cache()

    # Regex to match kernel package names
    # Matches: linux-image-6.8.0-88-generic, linux-headers-6.8.0-88, etc.
    kernel_pattern = re.compile(
        r'^linux-(image|headers|modules|modules-extra)-'
        r'(?:unsigned-)?'
        r'(\d+\.\d+\.\d+-\d+)'
        r'(?:-[a-z]+)?$'
    )

    # Group packages by version string
    version_groups = defaultdict(lambda: None)

    for pkg_name in cache.keys():
        match = kernel_pattern.match(pkg_name)
        if not match:
            continue

        pkg = cache[pkg_name]
        if not pkg.is_installed:
            continue

        version_str = match.group(2)  # e.g., "6.8.0-88"

        # Create or get the kernel group for this version
        if version_groups[version_str] is None:
            version_groups[version_str] = KernelPackageGroup(KernelVersion(version_str))

        # Check if package is manually installed
        is_manual = pkg.is_auto_installed == False
        version_groups[version_str].add_package(pkg_name, is_manual)

    # Mark running kernel
    running_version = get_running_kernel_version()
    if running_version and running_version in version_groups:
        version_groups[running_version].is_running = True

    # Group by kernel series
    by_series = defaultdict(list)
    for version_str, group in version_groups.items():
        by_series[group.version.series].append(group)

    # Sort each series by version (newest first)
    for series in by_series:
        by_series[series].sort(key=lambda g: g.version, reverse=True)

    return by_series


def print_current_state(kernels_by_series):
    """Print the current state of installed kernels."""
    print("\n" + "="*70)
    print("CURRENT KERNEL PACKAGE STATE")
    print("="*70)

    if not kernels_by_series:
        print("No kernel packages found.")
        return

    running_version = get_running_kernel_version()
    if running_version:
        print(f"\nRunning kernel: {running_version}")

    for series in sorted(kernels_by_series.keys(), reverse=True):
        groups = kernels_by_series[series]
        print(f"\n{series}.x series ({len(groups)} version(s) installed):")
        print("-" * 70)

        for group in groups:
            running_marker = " ← RUNNING" if group.is_running else ""
            print(f"  Version {group.version.original}{running_marker}")

            for pkg_name, is_manual in sorted(group.packages):
                status = "MANUAL" if is_manual else "AUTO  "
                print(f"    [{status}] {pkg_name}")

    print("\n" + "="*70)


def mark_packages(kernels_by_series, dry_run=False):
    """
    Mark kernel packages according to the retention policy.

    Policy per series:
    - Keep the 2 most recent versions as manual
    - Mark all older versions as auto-installed
    - Never touch the running kernel (always keep manual)
    """
    cache = apt.Cache()
    changes = []

    running_version = get_running_kernel_version()

    print("\n" + "="*70)
    print("APPLYING RETENTION POLICY")
    print("="*70)
    print("\nPolicy: Keep 2 most recent kernel versions per series as manual")
    print("        Mark older versions as auto-installed")
    print("        Always preserve the running kernel\n")

    for series in sorted(kernels_by_series.keys(), reverse=True):
        groups = kernels_by_series[series]
        print(f"\n{series}.x series:")
        print("-" * 70)

        # Determine which versions to keep
        versions_to_keep_manual = set()

        # Always keep the running kernel
        for group in groups:
            if group.is_running:
                versions_to_keep_manual.add(group.version.original)
                print(f"  {group.version.original}: KEEP (running kernel)")

        # Keep the 2 most recent versions
        for i, group in enumerate(groups[:2]):
            if group.version.original not in versions_to_keep_manual:
                versions_to_keep_manual.add(group.version.original)
                reason = "most recent" if i == 0 else "2nd most recent"
                print(f"  {group.version.original}: KEEP ({reason})")

        # Process all versions and mark them correctly
        for group in groups:
            if group.version.original in versions_to_keep_manual:
                # This version should be kept - ensure all packages are manual
                for pkg_name, is_manual in group.packages:
                    if not is_manual:  # Currently auto, should be manual
                        changes.append((pkg_name, "auto", "manual"))
            else:
                # This version should be eligible for removal - mark as auto
                print(f"  {group.version.original}: MARK AUTO (older version)")
                for pkg_name, is_manual in group.packages:
                    if is_manual:  # Currently manual, should be auto
                        changes.append((pkg_name, "manual", "auto"))

    # Apply changes
    if changes:
        print("\n" + "="*70)
        print("CHANGES TO BE APPLIED")
        print("="*70)
        print(f"\nTotal packages to change: {len(changes)}\n")

        for pkg_name, old_state, new_state in changes:
            print(f"  {pkg_name}: {old_state} → {new_state}")

        if dry_run:
            print("\n[DRY RUN] No changes applied. Run without --dry-run to apply.")
        else:
            print("\nApplying changes...")
            for pkg_name, old_state, new_state in changes:
                try:
                    pkg = cache[pkg_name]
                    if new_state == "auto":
                        pkg.mark_auto()
                        print(f"  ✓ Marked {pkg_name} as auto-installed")
                    elif new_state == "manual":
                        pkg.mark_manual()
                        print(f"  ✓ Marked {pkg_name} as manually-installed")
                except Exception as e:
                    print(f"  ✗ Failed to mark {pkg_name}: {e}")
            cache.commit()

        print("\n" + "="*70)
    else:
        print("\n" + "="*70)
        print("NO CHANGES NEEDED")
        print("="*70)
        print("\nAll kernel packages are already correctly marked.")
        print("\n" + "="*70)


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Ensure kernel packages are marked correctly per retention policy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This script helps manage kernel packages by ensuring they are marked correctly
according to the retention policy. Packages within the retention policy are
marked as manually-installed to preserve them. Packages outside the retention
policy are marked as auto-installed, allowing apt autoremove to clean them up.

The retention policy keeps:
  - The 2 most recent kernel versions in each series (major.minor)
  - The currently running kernel (always preserved)

Example:
  If you have kernels 6.8.0-86, 6.8.0-87, 6.8.0-88 installed,
  the script will ensure 6.8.0-88 and 6.8.0-87 are marked as manual,
  and mark 6.8.0-86 as auto-installed.

After running this script, you can run:
  sudo apt autoremove
to remove the older kernel packages.
        """
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be changed without making changes'
    )

    parser.add_argument(
        '--apply',
        action='store_true',
        help='Apply the changes (requires root)'
    )

    args = parser.parse_args()

    # Check for root if applying changes
    if args.apply and os.geteuid() != 0:
        print("ERROR: Root privileges required to modify package marks.")
        print("Run with: sudo python3 scan-kernel-packages.py --apply")
        sys.exit(1)

    # Find all kernel packages
    kernels_by_series = find_kernel_packages()

    # Show current state
    print_current_state(kernels_by_series)

    # Show what would be changed or apply changes
    if args.apply or args.dry_run:
        mark_packages(kernels_by_series, dry_run=args.dry_run)
    else:
        print("\nRun with --dry-run to see proposed changes")
        print("Run with --apply to apply changes (requires root)")

    print()


if __name__ == "__main__":
    main()
