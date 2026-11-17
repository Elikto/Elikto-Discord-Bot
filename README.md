# Elikto Discord Bot — catalogue global

Catalogue automatique du serveur Discord **Les notes d'Elikto**.

**Sans NAS ni PC allume :** le workflow GitHub Actions s'execute toutes les 6 heures (00:23, 06:23, 12:23, 18:23 UTC), avec possibilite de lancement manuel. GitHub peut retarder ou ignorer certaines executions programmees.

## Fonctions de cette premiere version

- Lit les categories et les salons textuels accessibles publiquement.
- Indexe les forums et leurs publications actives **et archivees**.
- Classe par categorie puis par nom de salon et titre de publication.
- Cree un catalogue dans un salon textuel `#catalogue` avec des liens vers les fiches d'origine.
- Actualise ses propres messages sans doublons, et supprime uniquement ses propres anciennes pages de catalogue devenues inutiles.
- N'efface, ne deplace et ne modifie **aucune publication originale**.
- Exclut les salons NSFW, ceux dont la categorie ou le salon est masque a `@everyone`, et les identifiants indiques dans `EXCLUDED_CHANNEL_IDS`.
- S'arrete en cas d'erreur de lecture des archives plutot que de publier un catalogue incomplet.

Le filtre est volontairement prudent : les salons visibles uniquement par des roles membres ne sont pas recenses dans un index public. La configuration des permissions de Discord peut comporter des cas complexes : verifie la visibilite du salon catalogue avant la premiere synchronisation.

**Pas encore implemente :** detection et validation automatique des changements d'adresse de sites, recherche interactive `/chercher`, notifications instantanees. Une edition via GitHub Actions fonctionne ponctuellement, et non 24 h/24.

## Configuration (une seule fois)

1. Invite le bot **scan stream** sur le serveur `1430984645012357263`. Son Application ID est `1572061260231737395`.
2. Reautorise le bot avec la permission **Gerer les salons** (`MANAGE_CHANNELS`) : elle est necessaire uniquement pour la creation initiale de `#catalogue`. Tu peux la retirer ensuite ; le bot retrouvera le salon via son marqueur.
3. Dans GitHub, ouvre **Settings > Secrets and variables > Actions**.
4. Dans **Secrets**, cree `DISCORD_BOT_TOKEN` avec le jeton du bot recupere dans le Discord Developer Portal. **Ne publie jamais ce jeton dans un fichier, un commentaire ou une conversation.**
5. Facultatif : variable `EXCLUDED_CHANNEL_IDS` (liste d'identifiants separes par des virgules).
6. Ouvre **Actions > Synchroniser le catalogue Discord > Run workflow**. Le bot cree automatiquement `#catalogue` a la racine du serveur, puis importe les publications visibles. Le salon sera reutilise aux executions suivantes.

**Il n'est pas necessaire de creer le salon manuellement ni de renseigner `CATALOG_CHANNEL_ID`.** Si tu possedes deja un salon texte `#catalogue` cree manuellement, tu peux toutefois preciser son ID dans la variable facultative `CATALOG_CHANNEL_ID` pour le reutiliser. Le bot refusera de modifier un salon existant sans marqueur ou ID explicite.

Le repo peut etre public, car **aucun secret n'est versionne**. Pour le rendre prive : **Settings > General > Danger Zone > Change repository visibility**.

## Permissions Discord minimales

- Voir les salons
- Voir les anciens messages
- Envoyer des messages
- Integrer les liens
- **Gerer les salons** pour creer automatiquement `#catalogue` (la permission peut etre retiree apres creation)

La creation est idempotente : identification du canal par un marqueur dans son sujet ; aucune duplication aux controles suivants. Si la creation echoue faute de permission, le workflow s'arrete avec un message explicite.

Pour de futurs modules, les permissions `Envoyer des messages dans les fils` et `Gerer les fils` pourront etre utiles. La permission `Administrateur` n'est pas necessaire.

## Developpement et tests

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

`sync_catalogue.py` utilise l'API REST Discord et quitte le programme apres chaque synchronisation. Aucun serveur permanent ni token GitHub personnel n'est necessaire.

## Securite et limites

Les messages d'origine restent dans leurs forums. Le catalogue reference les titres et liens, pas les descriptions completes. Un salon prive ne doit jamais etre mentionne dans un catalogue public. GitHub Actions execute le code programme en tant que bot avec les permissions Discord que tu lui as attribuees ; n'attribue pas de permissions inutiles.

Cette premiere version ne garantit pas l'acces a tous les forums historiques si Discord limite la lecture d'un forum ou si ses permissions empechent l'acces du bot. Le rapport d'execution doit etre verifie apres le premier lancement.
