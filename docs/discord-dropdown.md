# Menu déroulant « Streaming HD miroir »

Le code du menu Discord est prêt, mais **ne doit pas être activé avant que
le gestionnaire des interactions soit déployé** : le bot de synchronisation
GitHub Actions n'est pas un processus Discord permanent.

## Fonctionnement

- En-tête compact avec une URL principale vérifiée.
- Menu « Choisir un site miroir » (jusqu'à 25 sites par menu).
- Au clic, Discord envoie un événement signé au gestionnaire HTTPS.
- Le gestionnaire vérifie la signature Ed25519, vérifie l'URL parmi les
  options du message puis répond avec **un bouton-lien éphémère**.
- Les sites hors ligne ou dont le HTML est « Index of / » restent signalés.
- Le suivi quotidien de tous les miroirs ne dépend pas du menu.

## Mise en service

1. Déployer ce dépôt sur Vercel (le gestionnaire est
   \`api/discord-interactions.mjs\`, route \`/api/discord-interactions\`).
2. Dans le portail Discord Developer, sélectionner **l'application du bot**
   puis copier la **Public Key** dans la variable d'environnement Vercel
   \`DISCORD_PUBLIC_KEY\`. Il s'agit de la clé *publique*, **jamais du bot token**.
3. Définir dans le portail Discord l'**Interactions Endpoint URL** :
   \`https://<domaine-du-projet>/api/discord-interactions\`.
   Discord teste la signature et un PING. L'URL doit rester publique et ne pas
   demander de connexion Vercel.
4. Vérifier qu'aucune autre intégration utilisant le même endpoint Discord
   (slash commands, interactions de boutons) ne sera interrompue :
   l'endpoint de ce projet ne traite actuellement que le menu de miroirs.
5. **Après** validation, dans GitHub → Settings → Secrets and variables →
   Actions → Variables, créer
   \`DISCORD_MIRROR_DROPDOWN_ENABLED\` = \`1\`.
6. Relancer le workflow « Synchroniser Discord et publier le catalogue web ».
   Le message existant est édité, sans création de doublon.

Ne pas définir la variable à \`1\` avant les étapes 1 à 4 : un menu
sans endpoint actif affiche « Interaction échouée » après sélection.

## Tests

\`\`\`bash
python -m unittest discover -s tests -v
node --test tests/test_discord_interactions.mjs
\`\`\`

Les URL restent consultables dans le site catalogue indépendamment de
l'activation du menu. Les mots de passe des fiches ne sont jamais envoyés
au gestionnaire : il reçoit uniquement l'option URL choisie.
