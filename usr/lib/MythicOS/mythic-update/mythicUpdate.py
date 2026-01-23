#!/usr/bin/python3
# -*- coding: utf-8 -*-

from Classes import RepositoryManager
from checkAPT import APTChecker

APP_NAME = "Gestionnaire de mise à jour"

def main():
    print(f"=== {APP_NAME} ===")
    repo = RepositoryManager()
    repo.ensure_repositories()

    apt = APTChecker()
    apt.update()
    apt.list_upgradable()

if __name__ == "__main__":
    main()
