# ClassBrief-Pluginquelle

Diese Quelle wird erst nach dem Fly-Deployment zu einem installierbaren Pluginpaket.

1. Die stabile HTTPS-Domain und `https://<domain>/mcp` mit einem MCP-Client verifizieren.
2. `mcp.json.example` nach `mcp.json` kopieren und ausschließlich die verifizierte URL einsetzen.
3. Den Ordner ohne `mcp.json.example` und ohne diese README als privates Plugin paketieren.
4. Die OAuth-Verbindung im ClassBrief-Fenster mit dem ClassBrief-Passwort freigeben. Das IServ-Passwort wird dafür nicht benötigt.

Die Automatikregeln werden auf der ClassBrief-Webseite gepflegt. Der Plugin-Skill synchronisiert daraus normale ChatGPT-Cloud-Aufgaben; geplante Läufe lösen ihr Ziel bei jeder Ausführung erneut gegen Stundenplan und Ausnahmen auf.

Zugangsdaten gehören weder in `mcp.json` noch in das Archiv. Die OAuth-Clientregistrierung erfolgt dynamisch.
