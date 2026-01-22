#!/usr/bin/python3
# -*- coding: utf-8 -*-

import os
import subprocess
import sys
from mintUpdate.APTRepository import APTRepository

class MintUpdateManager:
    def __init__(self):
        # Dépôts officiels Mint / Ubuntu
        self.repos = [
            APTRepository(name="Official Mint", uri="http://packages.linuxmint.com", distribution="ulyssa", components=["main", "upstream", "import"]),
            APTRepository(name="Ubuntu", uri="http://archive.ubuntu.com/ubuntu", distribution="focal", components=["main", "restricted", "universe", "multiverse"]),
        ]

        # Dépôt MythicOS
        self.mythic_repo = APTRepository(
            name="MythicOS",
            uri="http://packages.mythicos.hastag.fr/linuxmint",
            distribution="ulyssa",
            components=["main"]
        )

        # Ajout du dépôt MythicOS à la liste
        self.repos.append(self.mythic_repo)

    def list_repos(self):
        print("Liste des dépôts connus :")
        for repo in self.repos:
            print(f"- {repo.get_name()}: {repo.get_uri()} [{repo.get_distribution()}] Components: {', '.join(repo.get_components())}")

    def update_sources(self):
        """Met à jour les sources APT pour inclure tous les dépôts"""
        sources_list_dir = "/etc/apt/sources.list.d"
        if not os.path.exists(sources_list_dir):
            os.makedirs(sources_list_dir)
        sources_list = os.path.join(sources_list_dir, "mintupdate.list")

        lines = []
        for repo in self.repos:
            line = f"deb {repo.get_uri()} {repo.get_distribution()} {' '.join(repo.get_components())}\n"
            lines.append(line)

        with open(sources_list, "w") as f:
            f.writelines(lines)
        print(f"Fichier sources mis à jour : {sources_list}")

    def check_updates(self):
        """Vérifie les mises à jour disponibles sur tous les dépôts"""
        print("Mise à jour de la liste APT…")
        subprocess.run(["sudo", "apt", "update"])
        print("Liste des paquets pouvant être mis à jour :")
        subprocess.run(["apt", "list", "--upgradable"])

    def upgrade_all(self):
        """Met à jour tous les paquets"""
        subprocess.run(["sudo", "apt", "upgrade", "-y"])

if __name__ == "__main__":
    manager = MintUpdateManager()
    manager.list_repos()
    manager.update_sources()
    manager.check_updates()
