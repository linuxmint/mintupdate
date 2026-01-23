#!/usr/bin/python3
# -*- coding: utf-8 -*-

import os
import subprocess
import sys

from Classes import RepositoryManager, APP_NAME


def run_command(cmd):
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Erreur lors de l'exécution : {cmd}")
        print(e)


def main():
    print(f"Lancement de {APP_NAME}…")

    # 1) Assurer la présence du dépôt MythicOS
    repo = RepositoryManager()
    repo.ensure_repositories()

    # 2) Mise à jour de la liste des paquets
    print("Actualisation des dépôts APT…")
    run_command(["apt", "update"])

    # 3) Lancer le vrai gestionnaire Mint (backend)
    mint_update_path = "/usr/bin/mintupdate"

    if os.path.exists(mint_update_path):
        print("Lancement du backend MintUpdate…")
        os.execv(mint_update_path, [mint_update_path] + sys.argv[1:])
    else:
        print("Erreur : mintupdate introuvable.")


if __name__ == "__main__":
    main()
