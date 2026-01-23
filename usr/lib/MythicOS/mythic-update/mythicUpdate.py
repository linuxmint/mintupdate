#!/usr/bin/python3
# -*- coding: utf-8 -*-

import os
import sys
import subprocess

from Classes import RepositoryManager, APP_NAME


def main():
    print(f"Lancement de {APP_NAME}…")

    # 1) Assurer dépôt + clé + priorité
    repo = RepositoryManager()
    repo.ensure_repositories()

    # 2) Mise à jour APT
    subprocess.run(["apt", "update"], check=False)

    # 3) Lancer MintUpdate (backend existant)
    mintupdate_bin = "/usr/bin/mintupdate"

    if os.path.exists(mintupdate_bin):
        os.execv(mintupdate_bin, [mintupdate_bin] + sys.argv[1:])
    else:
        print("Erreur : mintupdate introuvable.")


if __name__ == "__main__":
    main()
