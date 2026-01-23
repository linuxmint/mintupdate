#!/usr/bin/python3
# -*- coding: utf-8 -*-

import os
import urllib.request

APP_NAME = "Gestionnaire de mise à jour"

# Dépôt MythicOS (OFFICIEL)
MYTHIC_REPO_LINE = (
    "deb [signed-by=/usr/share/keyrings/mythicos.gpg] "
    "https://packages.mythicos.hastag.fr/ stable main\n"
)

MYTHIC_LIST_FILE = "/etc/apt/sources.list.d/mythicos.list"

# Clé GPG MythicOS (telle quelle)
MYTHIC_KEY_URL = "https://packages.mythicos.hastag.fr/mythicos.gpg"
MYTHIC_KEY_PATH = "/usr/share/keyrings/mythicos.gpg"


class RepositoryManager:

    def ensure_repositories(self):
        self.ensure_key()
        self.ensure_repo()

    def ensure_key(self):
        if os.path.exists(MYTHIC_KEY_PATH):
            return

        print("Téléchargement de la clé GPG MythicOS…")

        os.makedirs("/usr/share/keyrings", exist_ok=True)
        urllib.request.urlretrieve(MYTHIC_KEY_URL, MYTHIC_KEY_PATH)
        os.chmod(MYTHIC_KEY_PATH, 0o644)

    def ensure_repo(self):
        if os.path.exists(MYTHIC_LIST_FILE):
            return

        print("Ajout du dépôt MythicOS…")

        with open(MYTHIC_LIST_FILE, "w", encoding="utf-8") as f:
            f.write(MYTHIC_REPO_LINE)
