from getpass import getpass

from app.services.security import hash_password

print(hash_password(getpass("ClassBrief-Passwort: ")))
