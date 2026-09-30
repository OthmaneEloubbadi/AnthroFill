/*
 * i18n.js — shared French/English translation helper for the Data
 * Operations Portal pages (index.html, manual_history.html,
 * nom_complets.html, auto_history.html, ...).
 *
 * USAGE
 * -----
 * 1. Include this file in every page, near the top of <body> or in <head>:
 *      <script src="i18n.js"></script>
 *
 * 2. Tag static HTML text with data-i18n attributes:
 *      <h2 data-i18n="review_title">Révision des Modifications Manuelles</h2>
 *    - data-i18n="key"        -> sets el.textContent
 *    - data-i18n-html="key"   -> sets el.innerHTML (use when the string
 *                                 contains markup like <strong>)
 *    - data-i18n-title="key"  -> sets el.title (for tooltips)
 *    - data-i18n-placeholder="key" -> sets el.placeholder (for inputs)
 *
 * 3. For dynamic strings inside your own <script> blocks, call:
 *      t('some_key')
 *      t('some_key', { n: 5, msg: err.message })   // fills {n} / {msg}
 *
 * 4. Add a fr/en pair for every new key you use in I18N.entries below.
 *    Keys are shared across ALL pages in one dictionary, so reuse a key
 *    (e.g. "btn_cancel") wherever it means the same thing everywhere.
 *
 * 5. The language toggle button (FR/EN) is injected automatically in the
 *    top-right corner of every page that includes this file. Language
 *    choice is stored in localStorage and applies across all pages.
 *
 * 6. If a page needs to react after the language changes (e.g. re-render
 *    a table so its dynamic rows re-translate), register a callback:
 *      onLanguageChange(() => renderTable());
 *    It fires once on page load and again every time the language toggles.
 */

(function () {
  'use strict';

  const LANG_STORAGE_KEY = 'dopPortalLang';

  // ---------------------------------------------------------------------
  // Translation dictionary. Add new keys here as you tag more pages.
  // Every key should exist in BOTH fr and en.
  // ---------------------------------------------------------------------
  const I18N = {
    fr: {
      // --- Shared / global ---
      page_title: 'Portail des Opérations de Données',
      btn_back: 'Retour',
      btn_cancel: 'Annuler',
      btn_confirm: 'Confirmer',
      loading: 'Chargement...',

      // --- Landing page (index.html) ---
      landing_title: 'PORTAIL DES OPÉRATIONS DE DONNÉES',
      btn_access: 'Accéder aux Modifications Manuelles',

      // --- Manual changes review (index.html) ---
      review_title: 'Révision des Modifications Manuelles',
      btn_export: 'Exporter Nom & Prénom',
      btn_export_exporting: 'Exportation...',
      btn_clear_data: 'Effacer les Données Enregistrées',
      btn_manual_history: 'Historique Manuel',
      btn_auto_history: 'Historique Auto',
      btn_noms_complets: 'Noms complets',
      btn_autofill: 'Remplissage Auto (Correspondances Exactes)',
      btn_autofill_checking: 'Vérification de la référence...',
      btn_rewind: '↺ Revenir en Arrière',
      btn_rewind_running: 'Retour en cours...',
      rewind_title: 'Réinitialise immédiatement STAGING + historique juste après la dernière exécution du script — pas besoin de relancer le script de 8 minutes.',
      btn_run_script: 'Exécuter le Script',
      label_show_per_page: 'Afficher par page :',
      option_all: ' TOUT',
      total_unresolved: 'Total Non Résolus : {n}',
      btn_prev: '< Précédent',
      btn_next: 'Suivant >',
      btn_push_all: 'Tout Pousser (cette page)',
      btn_push_all_progress: 'Envoi {n} / {total}...',
      th_prenom: 'PRENOM',
      th_nom: 'NOM',
      th_assign_gender: 'ASSIGNER LE GENRE',
      empty_records_html: 'Aucun enregistrement non résolu chargé. Cliquez sur <strong>Exécuter le Script</strong> pour lancer la correspondance et réviser les noms non résolus.',
      no_names_found: 'Aucun nom non résolu trouvé.',
      page_info: 'Page {page} sur {total}',
      select_placeholder: '-- Sélectionner --',
      push_prenom: 'Pousser prénom',
      push_nom: 'Pousser nom',
      modal_title: 'Détails de Connexion à la Base de Données',
      label_sql_server: 'Hôte / Nom du Serveur SQL',
      label_sql_db: 'Nom de la Base de Données',
      label_sql_table: 'Table Cible',
      label_sql_user: "Nom d'Utilisateur SQL",
      label_sql_pass: 'Mot de Passe SQL',
      btn_run_fetch: 'Exécuter le Script & Récupérer les Nulls',
      session_restored: '🔄 Session restaurée — <strong>{n}</strong> enregistrement(s) non résolu(s) encore chargé(s).',
      executing_script: '⚙️ <strong>Exécution du script en cours...</strong> Veuillez patienter.',
      script_complete: '✅ <strong>Script terminé !</strong> Chargement des noms non résolus...',
      err_run_script: "Erreur lors de l'exécution du script : {msg}",
      err_connect_backend: 'Échec de la connexion au serveur backend.',
      err_load_unresolved: 'Erreur lors du chargement des noms non résolus : {msg}',
      loaded_unresolved: ' <strong>{n}</strong> enregistrement(s) non résolu(s) chargé(s).',
      err_fetch_unresolved: 'Échec de la récupération des noms non résolus.',
      need_db_table_autofill: 'Veuillez spécifier la Base de Données et la Table (fenêtre Exécuter le Script) avant le remplissage automatique.',
      scanning_null_rows: '🔎 <strong>Analyse des lignes NULL (PRENOM + NOM renseignés) pour des correspondances exactes...</strong>',
      err_autofill: 'Erreur lors du remplissage automatique depuis la référence : {msg}',
      autofill_done: "✅ <strong>{updated}</strong> ligne(s) remplie(s) automatiquement par correspondance exacte (vérifié {checked} ligne(s) NULL avec PRENOM + NOM renseignés). Les lignes sans correspondance exacte, sans PRENOM, ou ayant déjà un GENRE n'ont pas été modifiées.",
      err_autofill_endpoint: "Échec de l'accès au point de terminaison de remplissage automatique.",
      need_db_table_rewind: 'Veuillez spécifier la Base de Données et la Table (fenêtre Exécuter le Script) avant de revenir en arrière.',
      confirm_rewind: "Cela réinitialisera la table STAGING à l'état juste après la dernière exécution du script — annulant tout remplissage automatique, sauvegarde manuelle ou confirmation d'audit effectués depuis — et effacera l'historique Auto/Manuel. Cette action est irréversible. Continuer ?",
      rewinding: '↺ <strong>Retour de STAGING au dernier instantané du script en cours...</strong>',
      err_rewind: 'Erreur lors du retour en arrière : {msg}',
      rewind_complete: '✅ <strong>Retour en arrière terminé.</strong> {msg}',
      err_rewind_endpoint: "Échec de l'accès au point de terminaison de retour en arrière.",
      err_select_gender: 'Veuillez sélectionner MASCULIN ou FEMININ avant de pousser ce nom.',
      err_no_nom: 'Aucun NOM disponible à pousser pour cet enregistrement.',
      err_enrich: "Erreur lors de l'enrichissement des données de référence : {msg}",
      saved_prenom: '✅ <strong>{prenom}</strong> enregistré comme <strong>{gender}</strong> dans les données de référence{dbNote}{cascadeNote}.',
      saved_nom: '✅ <strong>{nom}</strong> (nom) enregistré comme <strong>{gender}</strong> dans les données de référence{dbNote}{cascadeNote}.',
      err_enrich_endpoint: "Échec de l'enrichissement des données de référence.",
      db_note_updated: ' et {n} ligne(s) de la base de données mise(s) à jour',
      db_note_error: ' (échec de la mise à jour de la base de données : {msg})',
      cascade_note: ', résolvant automatiquement {n} autre(s) enregistrement(s) correspondant(s)',
      cascade_note_error: ' (échec de la correspondance en cascade : {msg})',
      err_no_rows_selected: "Aucune ligne de cette page n'a de genre sélectionné.",
      push_all_done: '✅ <strong>{n}</strong> nom(s) poussé(s) depuis cette page{cascade}.',
      push_all_done_failures: '✅ <strong>{n}</strong> nom(s) poussé(s) depuis cette page{cascade}, <strong>{fail}</strong> échoué(s) (voir la console).',
      need_db_table_export: "Veuillez spécifier la Base de Données et la Table avant l'exportation.",
      no_unresolved_export: 'Aucun enregistrement non résolu trouvé ! Tous les enregistrements ont un genre assigné.',
      export_failed: "Échec de l'exportation : {msg}",

      // --- Manual history page (manual_history.html) ---
      manual_history_title: 'Historique des Envois Manuels',
      th_id: 'ID',
      th_genre_corrige: 'GENRE CORRIGÉ',
      th_correction: 'Correction',
      no_history_loaded: 'Aucun historique manuel chargé.',
      no_history_found: 'Aucun enregistrement d\'historique manuel trouvé.',
      history_restored: '🔄 Restauré — <strong>{n}</strong> enregistrement(s) d\'envoi manuel chargé(s).',
      history_loading: '⏳ Chargement de l\'historique des envois manuels...',
      err_load_history: 'Erreur lors du chargement de l\'historique manuel : {msg}',
      err_load_history_generic: "Échec du chargement de l'historique des envois manuels.",
      history_loaded: '📜 <strong>{n}</strong> enregistrement(s) d\'envoi manuel chargé(s).',
      select_gender_before_push: 'Veuillez sélectionner le genre corrigé avant de pousser.',
      no_prenom_to_push: 'Aucun PRENOM disponible à pousser pour cet enregistrement.',
      btn_pushing: 'Envoi...',
      btn_push: 'Pousser',
      err_push_correction: 'Erreur lors de l\'envoi de la correction : {msg}',
      err_push_correction_generic: "Échec de l'envoi de la correction.",
      correction_saved: '✅ <strong>{prenom}</strong> corrigé en <strong>{gender}</strong> dans les données de référence.',

      // --- Nom complets page (nom_complets.html) ---
      nom_complets_title: 'Noms Complets Sans Prénom',

      // --- Auto history page (auto_history.html) ---
      auto_history_title: 'Historique des Corrections Automatiques',

      // ================================================================
      // Additional page-specific translations
      // ================================================================

      // --- auto_history.html ---
      auto_history_data_operations_portal:
        'Historique Auto — Portail des Opérations de Données',

      auto_fill_history_exact_reference_matches:
        'Historique du Remplissage Auto (Correspondances Exactes)',

      back: 'Retour',
      show_per_page: 'Afficher par page:',

      '20': '20',
      '50': '50',
      '100': '100',
      '200': '200',

      prev: '< Précédent',
      next: 'Suivant >',

      id: 'ID',
      prenom: 'PRENOM',
      nom: 'NOM',

      genre_auto_assign: 'GENRE AUTO-ASSIGNÉ',
      correction: 'CORRECTION',

      select: '-- Sélectionner --',
      feminin: 'FEMININ',
      masculin: 'MASCULIN',
      push: 'Pousser',

      // --- manual_history.html ---
      manual_history_data_operations_portal:
        'Historique Manuel — Portail des Opérations de Données',

      manual_push_history:
        'Historique des Envois Manuels',

      all: 'TOUT',

      genre_correct: 'GENRE CORRIGÉ',

      // --- nom_complets.html ---
      nom_complets_data_operations_portal:
        'Noms complets — Portail des Opérations de Données',

      nom_complets:
        'Noms complets',

      export_nom_complets:
        'Exporter les Noms complets',

      '500000': '500000',

      push_all_this_page:
        'Tout Pousser (cette page)',

      nom_complet:
        'NOM COMPLET',

      assign_gender:
        'ASSIGNER LE GENRE',
      // --- Golden Rule popup ---
      golden_rule_title:
        '⚠ NOTE — <span class="btn-push-nom-inline">Push nom</span> est un cas d\'exception, pas un choix libre.',

      golden_rule_p1:
        'Ce portail alimente la table de référence des <strong>prénoms</strong>. Elle ne doit contenir <strong>que des prénoms</strong> — jamais de noms de famille.',

      golden_rule_p2:
        'Normalement, vous cliquez <span class="btn-push-inline">Push prenom</span> : le champ PRENOM de la ligne est poussé dans la référence.',

      golden_rule_p3:
        'Le bouton <span class="btn-push-nom-inline">Push nom</span> n\'existe que pour <strong>un seul cas précis</strong> : la personne qui a saisi la donnée a <strong>inversé les deux colonnes</strong> — le nom de famille a été mis dans le champ PRENOM, et le vrai prénom a été mis dans le champ NOM.',

      golden_rule_p4:
        'Vous ne devez cliquer <span class="btn-push-nom-inline">Push nom</span> que lorsque cette inversion est réellement présente, c\'est-à-dire :',

      golden_rule_li1:
        'le champ <strong>PRENOM</strong> affiché contient un <strong>nom de famille</strong>, et',

      golden_rule_li2:
        'le champ <strong>NOM</strong> affiché contient le <strong>vrai prénom</strong> de la personne.',

      golden_rule_p5:
        '<strong>Si le champ NOM contient un vrai nom de famille</strong> — donc aucune inversion — vous devez toujours utiliser <span class="btn-push-inline">Push prenom</span>, même si le PRENOM affiché vous semble étrange.',

      golden_rule_p6:
        '<strong>Conséquence :</strong> chaque nom de famille enregistré comme prénom dans la référence <strong>pollue la table de façon permanente</strong>. Cette entrée erronée sera ensuite réutilisée pour classer d\'autres lignes et <strong>attribuera un genre faux à des personnes sans lien</strong> avec la ligne d\'origine. Une seule erreur <span class="btn-push-nom-inline">Push nom</span> peut donc corrompre silencieusement des milliers de lignes futures.',

      golden_rule_never: 'Ne plus jamais afficher',
      golden_rule_understand: 'J\'ai compris',
      golden_rule_hide_reminder: 'Completely hide this reminder',
      // --- Additional keys for auto_history.html / nom_complets.html i18n wiring ---
      auto_history_loading: '⏳ Chargement de l\'historique du remplissage auto...',
      auto_history_loaded: '📜 <strong>{n}</strong> enregistrement(s) de remplissage auto chargé(s).',
      nom_complet_restored: '🔄 Restauré — <strong>{n}</strong> groupe(s) de noms complets chargé(s).',
      nom_complet_loading: '⏳ Chargement des lignes avec PRENOM vide...',
      nom_complet_loaded: ' <strong>{n}</strong> nom(s) complet(s) groupé(s) avec PRENOM vide chargé(s).',
      err_load_nom_complets: 'Erreur lors du chargement des noms complets : {msg}',
      err_load_nom_complets_generic: "Échec du chargement des noms complets. Exécutez d'abord le script depuis la page principale.",
      total_nom_complets: 'Total noms complets : {n}',
      no_empty_prenom_found: 'Aucun enregistrement avec PRENOM vide trouvé.',
      no_empty_prenom_loaded: 'Aucun enregistrement avec PRENOM vide chargé pour le moment.',
      records_grouped: '{n} enregistrement(s) groupé(s)',
      suggested_gender: '💡 Suggéré : <strong>{gender}</strong> ({pct}% de confiance) — survolez pour la raison',
      err_no_row_ids: "Cette ligne groupée n'a aucun ID de base de données à enregistrer.",
      err_save_gender: "Erreur lors de l'enregistrement du genre : {msg}",
      err_save_gender_generic: "Échec de l'enregistrement du genre dans la base de données.",
      err_no_rows_selected_ids: "Aucune ligne de cette page n'a de genre sélectionné (ou il manque des ID de base de données).",
      saving_progress: 'Enregistrement {n} / {total}...',
      push_all_nom_complet_done: '✅ <strong>{n}</strong> nom(s) complet(s) enregistré(s) depuis cette page.',
      push_all_nom_complet_done_failures: '✅ <strong>{n}</strong> nom(s) complet(s) enregistré(s) depuis cette page, <strong>{fail}</strong> échoué(s) (voir la console).',
      saved_nom_complet: '✅ <strong>{nomComplet}</strong> enregistré comme <strong>{gender}</strong> pour {n} ligne(s) de la base de données. Le fichier de référence n\'a pas été mis à jour.',
      err_configure_db_first: "Veuillez configurer les paramètres de la base de données sur la page principale d'abord.",
      no_empty_prenom_export: 'Aucun enregistrement trouvé avec PRENOM vide et NOM renseigné.',
      err_export_records: "Erreur lors de l'exportation des enregistrements : {msg}",
      err_export_records_generic: "Échec de l'exportation des enregistrements."
    },

    en: {
      // --- Shared / global ---
      page_title: 'Data Operations Portal',
      btn_back: 'Back',
      btn_cancel: 'Cancel',
      btn_confirm: 'Confirm',
      loading: 'Loading...',

      // --- Landing page (index.html) ---
      landing_title: 'DATABASE OPERATIONS PORTAL',
      btn_access: 'Access Manual Changes',

      // --- Manual changes review (index.html) ---
      review_title: 'Manual Changes Review',
      btn_export: 'Export Nom & Prenom',
      btn_export_exporting: 'Exporting...',
      btn_clear_data: 'Clear Saved Data',
      btn_manual_history: 'Manual History',
      btn_auto_history: 'Auto History',
      btn_noms_complets: 'Nom complets',
      btn_autofill: 'Auto-Fill Exact Matches',
      btn_autofill_checking: 'Checking reference...',
      btn_rewind: '↺ Rewind',
      btn_rewind_running: 'Rewinding...',
      rewind_title: 'Instantly resets STAGING + history back to right after the last script run — no need to re-run the 8-minute script.',
      btn_run_script: 'Run Script',
      label_show_per_page: 'Show per page:',
      option_all: ' ALL',
      total_unresolved: 'Total Unresolved: {n}',
      btn_prev: '< Prev',
      btn_next: 'Next >',
      btn_push_all: 'Push All (this page)',
      btn_push_all_progress: 'Pushing {n} / {total}...',
      th_prenom: 'PRENOM',
      th_nom: 'NOM',
      th_assign_gender: 'ASSIGN GENDER',
      empty_records_html: 'No unresolved records loaded. Click <strong>Run Script</strong> to execute matching and review unresolved names.',
      no_names_found: 'No unresolved names found.',
      page_info: 'Page {page} of {total}',
      select_placeholder: '-- Select --',
      push_prenom: 'Push prenom',
      push_nom: 'Push nom',
      modal_title: 'Database Connection Details',
      label_sql_server: 'SQL Server Host IP / Name',
      label_sql_db: 'Database Name',
      label_sql_table: 'Target Table',
      label_sql_user: 'SQL Username',
      label_sql_pass: 'SQL Password',
      btn_run_fetch: 'Run Script & Fetch Nulls',
      session_restored: '🔄 Restored session — <strong>{n}</strong> unresolved record(s) still loaded.',
      executing_script: '⚙️ <strong>Executing matching script...</strong> Please wait.',
      script_complete: '✅ <strong>Script complete!</strong> Loading unresolved names...',
      err_run_script: 'Error executing script: {msg}',
      err_connect_backend: 'Failed to connect to backend server.',
      err_load_unresolved: 'Error loading unresolved names: {msg}',
      loaded_unresolved: ' Loaded <strong>{n}</strong> unresolved record(s).',
      err_fetch_unresolved: 'Failed to fetch unresolved names.',
      need_db_table_autofill: 'Please specify both the Database and Table name (Run Script modal) before auto-filling.',
      scanning_null_rows: '🔎 <strong>Scanning NULL rows (PRENOM + NOM both filled) for exact reference matches...</strong>',
      err_autofill: 'Error auto-filling from reference: {msg}',
      autofill_done: '✅ <strong>{updated}</strong> row(s) auto-filled from an exact reference match (checked {checked} NULL row(s) with PRENOM + NOM both filled). Rows with no exact match, an empty PRENOM, or an existing GENRE were left untouched.',
      err_autofill_endpoint: 'Failed to reach the auto-fill endpoint.',
      need_db_table_rewind: 'Please specify both the Database and Table name (Run Script modal) before rewinding.',
      confirm_rewind: 'This will reset the STAGING table back to the state right after the last script run — discarding any Auto-Fill, manual save, or audit-confirm changes made since then — and clear Auto/Manual history. This cannot be undone. Continue?',
      rewinding: '↺ <strong>Rewinding STAGING to the last script-run snapshot...</strong>',
      err_rewind: 'Error rewinding: {msg}',
      rewind_complete: '✅ <strong>Rewind complete.</strong> {msg}',
      err_rewind_endpoint: 'Failed to reach the rewind endpoint.',
      err_select_gender: 'Please select MASCULIN or FEMININ before pushing this name.',
      err_no_nom: 'No NOM available to push for this record.',
      err_enrich: 'Error enriching reference data: {msg}',
      saved_prenom: '✅ Saved <strong>{prenom}</strong> as <strong>{gender}</strong> in the reference dataset{dbNote}{cascadeNote}.',
      saved_nom: '✅ Saved <strong>{nom}</strong> (nom) as <strong>{gender}</strong> in the reference dataset{dbNote}{cascadeNote}.',
      err_enrich_endpoint: 'Failed to enrich the reference dataset.',
      db_note_updated: ' and updated {n} database row(s)',
      db_note_error: ' (database update failed: {msg})',
      cascade_note: ', auto-resolving {n} other matching record(s)',
      cascade_note_error: ' (cascade match failed: {msg})',
      err_no_rows_selected: 'No rows on this page have a gender selected yet.',
      push_all_done: '✅ Pushed <strong>{n}</strong> name(s) from this page{cascade}.',
      push_all_done_failures: '✅ Pushed <strong>{n}</strong> name(s) from this page{cascade}, <strong>{fail}</strong> failed (see console).',
      need_db_table_export: 'Please specify both the Database and Table name before exporting.',
      no_unresolved_export: 'No unresolved records found! All records have assigned genders.',
      export_failed: 'Export failed: {msg}',
      // --- Golden Rule popup ---
      golden_rule_title:
        '⚠ NOTE — <span class="btn-push-nom-inline">Push nom</span> is an exceptional case, not a free choice.',

      golden_rule_p1:
        'This portal feeds the reference table of <strong>first names</strong>. It must contain <strong>first names only</strong> — never family names.',

      golden_rule_p2:
        'Normally, you click <span class="btn-push-inline">Push prenom</span>: the row\'s PRENOM field is pushed into the reference.',

      golden_rule_p3:
        'The <span class="btn-push-nom-inline">Push nom</span> button exists for <strong>one specific case only</strong>: the person who entered the data <strong>swapped the two columns</strong> — the family name was put in the PRENOM field, and the real first name was put in the NOM field.',

      golden_rule_p4:
        'You should only click <span class="btn-push-nom-inline">Push nom</span> when this swap is actually present, that is:',

      golden_rule_li1:
        'the displayed <strong>PRENOM</strong> field contains a <strong>family name</strong>, and',

      golden_rule_li2:
        'the displayed <strong>NOM</strong> field contains the person\'s <strong>real first name</strong>.',

      golden_rule_p5:
        '<strong>If the NOM field contains a real family name</strong> — so no swap — you must always use <span class="btn-push-inline">Push prenom</span>, even if the displayed PRENOM looks strange to you.',

      golden_rule_p6:
        '<strong>Consequence:</strong> every family name saved as a first name in the reference <strong>permanently pollutes the table</strong>. That wrong entry will then be reused to classify other rows and <strong>will assign a false gender to people unrelated</strong> to the original row. A single <span class="btn-push-nom-inline">Push nom</span> mistake can therefore silently corrupt thousands of future rows.',

      golden_rule_never: 'Never show again',
      golden_rule_understand: 'I understand',
      // --- Manual history page (manual_history.html) ---
      manual_history_title: 'Manual Push History',
      th_id: 'ID',
      th_genre_corrige: 'GENRE CORRECTÉ',
      th_correction: 'Correction',
      no_history_loaded: 'No manual history loaded yet.',
      no_history_found: 'No manual history records found.',
      history_restored: '🔄 Restored — <strong>{n}</strong> manual push record(s) loaded.',
      history_loading: '⏳ Loading manual push history...',
      err_load_history: 'Error loading manual history: {msg}',
      err_load_history_generic: 'Failed to load manual push history.',
      history_loaded: '📜 Loaded <strong>{n}</strong> manual push record(s).',
      select_gender_before_push: 'Please select the corrected gender before pushing.',
      no_prenom_to_push: 'No PRENOM available to push for this record.',
      btn_pushing: 'Pushing...',
      btn_push: 'Push',
      err_push_correction: 'Error pushing correction: {msg}',
      err_push_correction_generic: 'Failed to push the correction.',
      correction_saved: '✅ Corrected <strong>{prenom}</strong> to <strong>{gender}</strong> in the reference dataset.',
      golden_rule_hide_reminder: 'Completely hide this reminder',
      // --- Nom complets page (nom_complets.html) ---
      nom_complets_title: 'Full Names Missing First Name',

      // --- Auto history page (auto_history.html) ---
      auto_history_title: 'Automatic Correction History',

      // ================================================================
      // Additional page-specific translations
      // ================================================================

      // --- auto_history.html ---
      auto_history_data_operations_portal:
        'Auto History — Data Operations Portal',

      auto_fill_history_exact_reference_matches:
        'Auto-Fill History (Exact Reference Matches)',

      back: 'Back',
      show_per_page: 'Show per page:',

      '20': '20',
      '50': '50',
      '100': '100',
      '200': '200',

      prev: '< Prev',
      next: 'Next >',

      id: 'ID',
      prenom: 'PRENOM',
      nom: 'NOM',

      genre_auto_assign: 'GENRE AUTO-ASSIGNÉ',
      correction: 'CORRECTION',

      select: '-- Select --',
      feminin: 'FEMININ',
      masculin: 'MASCULIN',
      push: 'Push',

      // --- manual_history.html ---
      manual_history_data_operations_portal:
        'Manual History — Data Operations Portal',

      manual_push_history:
        'Manual Push History',

      all: 'ALL',

      genre_correct: 'GENRE CORRECTÉ',

      // --- nom_complets.html ---
      nom_complets_data_operations_portal:
        'Nom complets — Data Operations Portal',

      nom_complets:
        'Nom complets',

      export_nom_complets:
        'Export Nom complets',

      '500000': '500000',

      push_all_this_page:
        'Push All (this page)',

      nom_complet:
        'NOM COMPLET',

      assign_gender:
        'ASSIGN GENDER',

      // --- Additional keys for auto_history.html / nom_complets.html i18n wiring ---
      auto_history_loading: '⏳ Loading auto-fill history...',
      auto_history_loaded: '📜 Loaded <strong>{n}</strong> auto-fill record(s).',
      nom_complet_restored: '🔄 Restored — <strong>{n}</strong> nom complet group(s) loaded.',
      nom_complet_loading: '⏳ Loading rows with empty PRENOM...',
      nom_complet_loaded: ' Loaded <strong>{n}</strong> grouped nom complet(s) with empty PRENOM.',
      err_load_nom_complets: 'Error loading nom complets: {msg}',
      err_load_nom_complets_generic: 'Failed to load nom complets. Run the script from the main page first.',
      total_nom_complets: 'Total nom complets: {n}',
      no_empty_prenom_found: 'No empty-PRENOM records found.',
      no_empty_prenom_loaded: 'No empty-prenom records loaded yet.',
      records_grouped: '{n} record(s) grouped',
      suggested_gender: '💡 Suggested: <strong>{gender}</strong> ({pct}% confidence) — hover for reason',
      err_no_row_ids: 'This grouped row has no database IDs to save.',
      err_save_gender: 'Error saving gender: {msg}',
      err_save_gender_generic: 'Failed to save gender to the database.',
      err_no_rows_selected_ids: 'No rows on this page have a gender selected yet (or are missing database IDs).',
      saving_progress: 'Saving {n} / {total}...',
      push_all_nom_complet_done: '✅ Saved <strong>{n}</strong> nom complet(s) from this page.',
      push_all_nom_complet_done_failures: '✅ Saved <strong>{n}</strong> nom complet(s) from this page, <strong>{fail}</strong> failed (see console).',
      saved_nom_complet: '✅ Saved <strong>{nomComplet}</strong> as <strong>{gender}</strong> for {n} database row(s). Reference file was not updated.',
      err_configure_db_first: 'Please configure your database settings on the main page first.',
      no_empty_prenom_export: 'No records found with empty PRENOM and non-empty NOM.',
      err_export_records: 'Error exporting records: {msg}',
      err_export_records_generic: 'Failed to export records.'
    }
  };

  // ---------------------------------------------------------------------
  // Core helpers
  // ---------------------------------------------------------------------

  function getLang() {
    return localStorage.getItem(LANG_STORAGE_KEY) || 'en';
  }

  function setLang(lang) {
    localStorage.setItem(LANG_STORAGE_KEY, lang);
  }

  /**
   * t(key, vars) — look up a translated string for the current language
   * and fill in {placeholder} tokens from vars.
   *
   * Example:
   *   t('total_unresolved', { n: 12 })
   *   -> "Total Non Résolus : 12"
   *
   * Falls back to French, then to the raw key, if a translation is missing.
   */
  function t(key, vars) {
    const lang = getLang();
    const dict = I18N[lang] || I18N.fr;

    let str = dict[key] !== undefined
      ? dict[key]
      : (I18N.fr[key] !== undefined ? I18N.fr[key] : key);

    if (vars) {
      Object.keys(vars).forEach(function (k) {
        str = str.replace(
          new RegExp('\\{' + k + '\\}', 'g'),
          vars[k]
        );
      });
    }

    return str;
  }

  // Callbacks other pages can register to re-run their own rendering
  // whenever the language changes.
  const languageChangeCallbacks = [];

  function onLanguageChange(callback) {
    if (typeof callback === 'function') {
      languageChangeCallbacks.push(callback);
    }
  }

  /**
   * Applies the current language to every tagged element on the page,
   * updates <title>, updates the toggle button label, then runs any
   * registered onLanguageChange callbacks.
   */
  function applyLanguage() {
    const lang = getLang();

    document.documentElement.lang = lang;

    const titleKey =
      document.title &&
      document.querySelector('title[data-i18n]')
        ? document
            .querySelector('title[data-i18n]')
            .getAttribute('data-i18n')
        : null;

    if (titleKey) {
      document.title = t(titleKey);
    }

    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      if (el.tagName === 'TITLE') return;

      el.textContent = t(
        el.getAttribute('data-i18n')
      );
    });

    document.querySelectorAll('[data-i18n-html]').forEach(function (el) {
      el.innerHTML = t(
        el.getAttribute('data-i18n-html')
      );
    });

    document.querySelectorAll('[data-i18n-title]').forEach(function (el) {
      el.setAttribute(
        'title',
        t(el.getAttribute('data-i18n-title'))
      );
    });

    document.querySelectorAll('[data-i18n-placeholder]').forEach(function (el) {
      el.setAttribute(
        'placeholder',
        t(el.getAttribute('data-i18n-placeholder'))
      );
    });

    const toggleBtn = document.getElementById('lang-toggle-btn');

    if (toggleBtn) {
      toggleBtn.textContent = lang === 'fr' ? 'EN' : 'FR';
    }

    languageChangeCallbacks.forEach(function (cb) {
      try {
        cb();
      } catch (e) {
        console.error(
          'i18n onLanguageChange callback failed:',
          e
        );
      }
    });
  }

  function toggleLanguage() {
    setLang(
      getLang() === 'fr'
        ? 'en'
        : 'fr'
    );

    applyLanguage();
  }

  // ---------------------------------------------------------------------
  // Auto-inject the toggle button + its styles once the DOM is ready, so
  // pages don't need to add any HTML/CSS themselves to get the button.
  // ---------------------------------------------------------------------

  function injectToggleButton() {
    if (document.getElementById('lang-toggle-btn')) return;

    const style = document.createElement('style');

    style.textContent =
      '.btn-lang-toggle {' +
      '  position: fixed;' +
      '  top: 1rem;' +
      '  right: 1rem;' +
      '  z-index: 1000;' +
      '  background-color: #1e293b;' +
      '  color: #fff;' +
      '  border: 1px solid rgba(255,255,255,0.2);' +
      '  padding: 0.5rem 1rem;' +
      '  font-size: 0.85rem;' +
      '  font-weight: 600;' +
      '  border-radius: 6px;' +
      '  cursor: pointer;' +
      '  font-family: inherit;' +
      '  transition: background-color 0.2s ease;' +
      '}' +
      '.btn-lang-toggle:hover {' +
      '  background-color: #334155;' +
      '}';

    document.head.appendChild(style);

    const btn = document.createElement('button');

    btn.id = 'lang-toggle-btn';
    btn.className = 'btn-lang-toggle';
    btn.type = 'button';

    btn.addEventListener(
      'click',
      toggleLanguage
    );

    document.body.insertBefore(
      btn,
      document.body.firstChild
    );
  }

  document.addEventListener(
    'DOMContentLoaded',
    function () {
      injectToggleButton();
      applyLanguage();
    }
  );

  // Expose what pages need on window.
  window.t = t;
  window.getLang = getLang;
  window.setLang = setLang;
  window.toggleLanguage = toggleLanguage;
  window.applyLanguage = applyLanguage;
  window.onLanguageChange = onLanguageChange;

})();