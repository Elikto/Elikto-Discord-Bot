# Elikto Discord Bot — catalogue global

## Site web et surveillance des adresses

**Site : https://elikto.github.io/Elikto-Discord-Bot/**

Le tableau de bord (dossier `site/`) comprend un menu unique de catégories,
une recherche, les fiches Discord et désormais une rubrique **Surveillance des sites** :

- Disponible (réponse HTTP 2xx-3xx, sans garantie de bon fonctionnement applicatif).
- À vérifier (première erreur 404/5xx/connexion, sans affirmer une panne).
- Inaccessible (deux contrôles successifs espacés d'au moins 48 h).
- Accès restreint (401/403/429, notamment les protections anti-bot).
- Adresse introuvable / contrôle impossible (pas de lien exploitable).
- Un historique daté des nouveaux titres, changements d'adresse, redirections,
  indisponibilités confirmées et rétablissements.
- Filtres par état, par catégorie et recherche. Les discussions restent à la fin.
- Un salon privé `#alertes-sites` est créé pour les administrateurs
  (la permission `Voir le salon` est refusée à @everyone) ; seules les
  transitions importantes déclenchent des messages.

### Mise à jour automatique et historique

`catalogue.yml` est lancé toutes les six heures (et lors des modifications).
`sync_catalogue.py` collecte les forums et fils visibles par @everyone.
`site_monitor.py` extrait les URL des **messages d'ouverture des fils**,
contrôle chaque adresse au maximum toutes les 48 heures et publie
`site/monitoring.json` ainsi que les statuts de `site/catalogue.json`.
`data/monitor_state.json`, sans identifiants de connexion, est sauvegardé
automatiquement dans GitHub pour conserver l'historique entre les lancements.

Un site peut changer de domaine par redirection : le nouveau domaine est alors
**signalé pour vérification**, sans remplacer automatiquement l'adresse officielle.
Une réponse HTTP ne prouve pas que le site est sûr ou que son contenu est correct.

### Suivi dédié : fil « Streaming HD miroir »

Le fil public **1430988094374547559** dispose d'un suivi de nom et d'adresse
spécifique (`thread_watch.py`), relancé toutes les six heures par GitHub
Actions.

- Quand **l'auteur du fil** renseigne une adresse valide dans son message ou
  annonce clairement « Nouvelle adresse : https://… », le bot met à jour
  **son propre message de suivi dans ce fil** (un seul message, jamais de doublon).
- Si l'auteur écrit « Nouveau nom : … », le bot peut aussi corriger le **titre
  du fil** lorsqu'il possède la permission **Gérer les fils**.
- Les anciennes adresses et les modifications de nom sont historisées dans
  `data/monitor_state.json` et présentées dans le tableau de bord.
- Si une ancienne adresse redirige vers un domaine différent, le bot le
  **signale sans supposer** qu'il s'agit du même site. Aucun changement de
  nom n'est déduit automatiquement d'un simple nom de domaine.
- Le bot **ne peut pas éditer le texte du message original d'un membre** :
  seul son message de suivi est corrigé. Aucune écriture n'a lieu si le texte
  source n'est pas lisible ou si plusieurs URL sont ambiguës.
- Si l'auteur modifie directement le titre du fil, le bot reflète ce titre
  lors de sa prochaine synchronisation.

**Prérequis :** activer `Message Content Intent` pour que les URL des
messages soient visibles au bot. Vérifier aussi `Écrire dans les fils` ;
`Gérer les fils` est nécessaire pour renommer les fils créés par autrui.

### Autorisation Discord indispensable

**Pour lire les URL des messages** (et non seulement leur titre), activer
`MESSAGE CONTENT INTENT` depuis
**Discord Developer Portal → Applications → scan stream → Bot → Privileged Gateway Intents**
et enregistrer. Sans cette autorisation, Discord masque le texte et les embeds
des messages au bot : les fiches restent dans le catalogue, mais sont indiquées
**« Adresse manquante »**, sans faux contrôle de disponibilité.

Le bot conserve uniquement le nom de la fiche, son lien Discord et l'URL
publique normalisée ; jamais le contenu intégral des messages ni les secrets
d'accès. Les hôtes privés, loopback et les redirections vers des adresses
non publiques sont refusés avant la connexion ; aucun jeton du bot ne
figure dans le site statique.


Catalogue automatique du serveur Discord **Les notes d'Elikto**.

**Sans NAS ni PC allume :** le workflow GitHub Actions s'execute toutes les 6 heures (00:23, 06:23, 12:23, 18:23 UTC), au changement du programme ou manuellement. GitHub peut retarder ou ignorer certaines executions programmees.

## Présentation : un seul message dans Discord

Le salon `#catalogue` contient **un seul tableau de bord** avec une carte d'accueil et des cartes de categories colorees. Les fiches sont regroupees sous leurs forums et sont accessibles directement depuis ce meme message via des mentions/liens Discord. Aucun lien de fiche n'est modifie dans le forum d'origine.

- **Un seul message de catalogue**, plutot que 8 messages separes.
- Une carte coloree par categorie (Streaming, Telechargement, Anti-pub, etc.).
- Compteurs et sous-rubriques classes alphabetiquement.
- Liens courts directs vers les fiches et vers les forums.
- Mise a jour automatique par GitHub Actions sans hebergement permanent.
- Si trop de fiches depassent la limite Discord (**4 000 caracteres de texte et 40 composants par message**), la page affiche un apercu et donne acces au reste par les liens des forums. Ce repli est affiche explicitement.
- Les anciennes cartes individuelles du bot sont retirees **apres** confirmation de la publication du tableau de bord unique. Les fiches et messages originaux des membres ne sont jamais modifies.

Les onglets interactifs sans navigation exigeraient un service de reponse aux interactions Discord ; un systeme reposant uniquement sur GitHub Actions ne peut pas repondre en temps reel aux clics de ces controles.

## Fonctions de cette premiere version

- Lit les categories et les salons textuels accessibles publiquement.
- Indexe les forums et leurs publications actives **et archivees**.
- Affiche un accueil soigne, une seule page avec compteurs, heure de synchronisation et couleurs/emoji par rubrique.\n- Regroupe les fiches par forum et les trie par ordre alphabetique, en privilegiant un index compact quand le contenu devient volumineux.\n- Classe les categories principales dans un ordre naturel : Streaming, IPTV, Anime, Telechargement, Anti-pub, Android, Jeux, Livres, puis les autres rubriques par ordre alphabetique.
- Cree un catalogue dans un salon textuel `#catalogue` avec des liens vers les fiches d'origine.
- Actualise son unique message sans doublons et retire les anciennes pages du bot devenues inutiles.
- N'efface, ne deplace et ne modifie **aucune publication originale**.
- Exclut les salons NSFW, ceux dont la categorie ou le salon est masque a `@everyone`, et les identifiants indiques dans `EXCLUDED_CHANNEL_IDS`.
- S'arrete en cas d'erreur de lecture des archives plutot que de publier un catalogue incomplet.

Le filtre est volontairement prudent : les salons visibles uniquement par des roles membres ne sont pas recenses dans un index public. La configuration des permissions de Discord peut comporter des cas complexes : verifie la visibilite du salon catalogue avant la premiere synchronisation.

**Limite actuelle :** la recherche interactive `/chercher` et les alertes instantanées ne sont pas disponibles, car le bot fonctionne par GitHub Actions. La surveillance HTTP et les alertes sur changement sont exécutées aux passages programmés, et uniquement pour les adresses effectivement détectées.

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

La creation est idempotente : identification des salons du bot par leur marqueur dans leur sujet ; aucune duplication aux controles suivants. Si la creation echoue faute de permission, le workflow s'arrete avec un message explicite.

Pour de futurs modules, les permissions `Envoyer des messages dans les fils` et `Gerer les fils` pourront etre utiles. La permission `Administrateur` n'est pas necessaire.

## Developpement et tests

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

`sync_catalogue.py` utilise l'API REST Discord et quitte le programme apres chaque synchronisation. Aucun serveur permanent ni token GitHub personnel n'est necessaire.

## Securite et limites

Les messages d'origine restent dans leurs forums. Le catalogue reference les titres et liens, pas les descriptions completes. Un salon prive ne doit jamais etre mentionne dans un catalogue public. GitHub Actions execute le code programme en tant que bot avec les permissions Discord que tu lui as attribuees ; n'attribue pas de permissions inutiles.

Tu peux epingler manuellement le tableau de bord unique dans Discord. Il est reutilise et actualise sans supprimer les publications originales.\n\nCette premiere version ne garantit pas l'acces a tous les forums historiques si Discord limite la lecture d'un forum ou si ses permissions empechent l'acces du bot. Le rapport d'execution doit etre verifie apres le premier lancement.
