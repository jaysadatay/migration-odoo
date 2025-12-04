#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from migration_fonction import *
import os
import json


#** Paramètres ****************************************************************
db_src = "infosaone13"
db_dst = "infosaone19"
#******************************************************************************

cnx,cr=GetCR(db_src)

cnx_src,cr_src=GetCR(db_src)
cnx_dst,cr_dst=GetCR(db_dst)






MigrationDonneesTable(db_src,db_dst,'res_company')

default={
#    'autopost_bills': 'ask',
}
MigrationTable(db_src,db_dst,'res_partner',text2jsonb=True,default=default)



sys.exit()





# ** Tables diverses **********************************************************
tables=[
    "blog_blog",
    "blog_post",
    "blog_tag",
    "blog_tag_category",
]
for table in tables:
    print(table)
    MigrationTable(db_src,db_dst,table,text2jsonb=True)




# ** Tables diverses **********************************************************
tables=[
    "blog_post_blog_tag_rel",
]
for table in tables:
    print(table)
    MigrationTable(db_src,db_dst,table)






# ** website_menu sera migré après website_page (à cause des page_id) *********




# ** Tables diverses **********************************************************
tables=[
    "website",
    "website_lang_rel",
    # "website_page",   # Migré plus tard après la gestion des vues
    #"website_track",   # Actvier plus tard pour ganger du temps de traitement
    #"website_visitor", # Actvier plus tard pour ganger du temps de traitement
]
for table in tables:
    print(table)
    MigrationTable(db_src,db_dst,table)

# ** Correction des IDs de langue (fr_FR: 27 dans v13 -> 30 dans v19) *********
SQL = """
    UPDATE website 
    SET default_lang_id = (SELECT id FROM res_lang WHERE code = 'fr_FR')
    WHERE default_lang_id IS NOT NULL;
"""
cr_dst.execute(SQL)
cnx_dst.commit()

SQL = """
    UPDATE website_lang_rel 
    SET lang_id = (SELECT id FROM res_lang WHERE code = 'fr_FR')
    WHERE lang_id NOT IN (SELECT id FROM res_lang);
"""
cr_dst.execute(SQL)
cnx_dst.commit()
#******************************************************************************


# ** Migration des vues ir_ui_view liées aux website_page ET vues personnalisées **
print("\n" + "="*70)
print("Migration des vues ir_ui_view (pages + vues personnalisées du site)")
print("="*70)

# Étape 1: Récupérer TOUTES les vues avec website_id dans Odoo 13
# (inclut les vues de pages ET les vues personnalisées comme footer, header, blog, etc.)
print("\n1. Récupération des vues personnalisées (website_id IS NOT NULL) dans Odoo 13...")
SQL = """
    SELECT DISTINCT v.id, v.name, v.key, v.type, v.arch_fs, 
           v.priority, v.model, v.inherit_id,
           v.mode, v.active, v.website_id
    FROM ir_ui_view v
    WHERE v.website_id IS NOT NULL
"""
cr_src.execute(SQL)
views_src = cr_src.fetchall()
print(f"   {len(views_src)} vues personnalisées trouvées dans Odoo 13")

for view in views_src:
    print(f"   - ID={view['id']}, key={view['key']}, name={view['name']}, website_id={view['website_id']}")

# Étape 2: Supprimer toutes les website_page et leurs vues dans Odoo 19
print("\n2. Suppression des website_page et vues personnalisées existantes dans Odoo 19...")

# Récupérer les view_id des pages existantes
SQL = "SELECT DISTINCT view_id FROM website_page WHERE view_id IS NOT NULL"
cr_dst.execute(SQL)
view_ids_to_delete = [row['view_id'] for row in cr_dst.fetchall()]
print(f"   {len(view_ids_to_delete)} vues liées aux pages: {view_ids_to_delete}")

# Récupérer aussi les vues créées par des migrations précédentes (is_origine_view_id IS NOT NULL)
SQL = "SELECT id FROM ir_ui_view WHERE is_origine_view_id IS NOT NULL"
cr_dst.execute(SQL)
view_ids_migrated = [row['id'] for row in cr_dst.fetchall()]
print(f"   {len(view_ids_migrated)} vues de migrations précédentes: {view_ids_migrated}")

# Récupérer aussi les vues personnalisées avec website_id (footer, header, etc.)
SQL = "SELECT id FROM ir_ui_view WHERE website_id IS NOT NULL"
cr_dst.execute(SQL)
view_ids_custom = [row['id'] for row in cr_dst.fetchall()]
print(f"   {len(view_ids_custom)} vues personnalisées (website_id IS NOT NULL): {view_ids_custom[:10]}...")

# Fusionner les trois listes
all_view_ids_to_delete = list(set(view_ids_to_delete + view_ids_migrated + view_ids_custom))
print(f"   Total: {len(all_view_ids_to_delete)} vues à supprimer")

# Supprimer les website_page
SQL = "DELETE FROM website_page"
cr_dst.execute(SQL)
print(f"   {cr_dst.rowcount} website_page supprimées")

# Supprimer TOUTES les vues (liées aux pages + migrations précédentes)
if all_view_ids_to_delete:
    SQL = "DELETE FROM ir_ui_view WHERE id IN %s"
    cr_dst.execute(SQL, (tuple(all_view_ids_to_delete),))
    print(f"   {cr_dst.rowcount} vues supprimées")

cnx_dst.commit()

# Étape 3: Insérer les vues d'Odoo 13 dans Odoo 19
print("\n3. Insertion des vues d'Odoo 13 dans Odoo 19...")

# Trier les vues pour que les vues parentes soient créées avant les vues enfants
# Les vues sans inherit_id d'abord, puis celles avec inherit_id
def get_view_order(views):
    """Trie les vues pour respecter les dépendances inherit_id"""
    view_ids = {v['id'] for v in views}
    ordered = []
    remaining = list(views)
    processed_ids = set()
    
    # Maximum 10 passes pour éviter les boucles infinies
    for _ in range(10):
        if not remaining:
            break
        still_remaining = []
        for view in remaining:
            # Vue sans inherit_id ou inherit_id hors de notre liste ou déjà traité
            if not view['inherit_id'] or view['inherit_id'] not in view_ids or view['inherit_id'] in processed_ids:
                ordered.append(view)
                processed_ids.add(view['id'])
            else:
                still_remaining.append(view)
        remaining = still_remaining
    
    # Ajouter les vues restantes (dépendances non résolues)
    ordered.extend(remaining)
    return ordered

views_src = get_view_order(views_src)
print(f"   Vues triées par dépendances")

# Mapping ancien view_id -> nouveau view_id
view_id_mapping = {}

for view in views_src:
    old_view_id = view['id']
    website_id = view['website_id']
    key = view['key']
    
    # Pour les vues de PAGES (mode=primary, sans inherit_id), on modifie la clé
    # pour éviter qu'Odoo les écrase lors des mises à jour de modules
    # Les vues d'héritage/extension (mode=extension ou avec inherit_id) gardent leur clé
    # pour être reconnues comme personnalisations du site
    if website_id and key and view['mode'] == 'primary' and not view['inherit_id']:
        # Ajouter un suffixe unique basé sur l'ancien ID
        key = f"{key}_migrated_{old_view_id}"
        print(f"   Clé modifiée pour vue de page: {view['key']} -> {key}")
    elif website_id and key:
        print(f"   Clé conservée pour vue d'héritage: {key} (mode={view['mode']})")
    
    # Trouver le inherit_id correspondant dans Odoo 19
    new_inherit_id = None
    if view['inherit_id']:
        old_inherit_id = view['inherit_id']
        
        # D'abord vérifier si la vue parente a déjà été migrée (dans notre mapping)
        if old_inherit_id in view_id_mapping:
            new_inherit_id = view_id_mapping[old_inherit_id]
            print(f"   inherit_id résolu via mapping: {old_inherit_id} -> {new_inherit_id}")
        else:
            # Sinon chercher par clé dans Odoo 19
            SQL = "SELECT key FROM ir_ui_view WHERE id = %s"
            cr_src.execute(SQL, [old_inherit_id])
            parent = cr_src.fetchone()
            if parent and parent['key']:
                SQL = "SELECT id FROM ir_ui_view WHERE key = %s LIMIT 1"
                cr_dst.execute(SQL, [parent['key']])
                new_parent = cr_dst.fetchone()
                if new_parent:
                    new_inherit_id = new_parent['id']
                    print(f"   inherit_id résolu via clé: {parent['key']} -> {new_inherit_id}")
        
        # Si inherit_id non résolu et mode=extension, on ne peut pas créer la vue
        if new_inherit_id is None and view['mode'] == 'extension':
            print(f"   ATTENTION: Vue {old_view_id} ({key}) ignorée - inherit_id non résolu et mode=extension")
            continue
    
    # Récupérer arch_db séparément (pour éviter les problèmes avec DISTINCT sur TEXT)
    SQL = "SELECT arch_db FROM ir_ui_view WHERE id = %s"
    cr_src.execute(SQL, [old_view_id])
    result = cr_src.fetchone()
    arch_db_value = result['arch_db'] if result else None
    
    print(f"   DEBUG: vue {old_view_id}, arch_db présent: {arch_db_value is not None}, longueur: {len(arch_db_value) if arch_db_value else 0}")
    
    # Convertir arch_db en JSONB (format Odoo 19)
    # Dans Odoo 13, arch_db est du texte brut, dans Odoo 19 c'est du JSONB
    arch_jsonb = None
    if arch_db_value:
        arch_str = arch_db_value if isinstance(arch_db_value, str) else str(arch_db_value)
        
        import re
        # Toujours mettre à jour le t-name pour qu'il corresponde à la clé de la vue
        # Le t-name dans le XML peut être différent de la clé (ex: website.homepage1 vs website.homepage)
        # On remplace le premier t-name trouvé par la nouvelle clé
        old_tname_match = re.search(r't-name=["\']([^"\']+)["\']', arch_str)
        if old_tname_match:
            old_tname = old_tname_match.group(1)
            if old_tname != key:
                arch_str = re.sub(
                    r't-name=["\'][^"\']+["\']',
                    f't-name="{key}"',
                    arch_str,
                    count=1  # Remplacer seulement le premier
                )
                print(f"   t-name mis à jour: {old_tname} -> {key}")
        
        arch_jsonb = json.dumps({"fr_FR": arch_str})
        print(f"   DEBUG: arch_jsonb généré, longueur: {len(arch_jsonb)}")
    else:
        print(f"   ATTENTION: arch_db vide pour vue {old_view_id} ({key})")
    
    # Insérer la nouvelle vue
    SQL = """
        INSERT INTO ir_ui_view 
            (name, key, type, arch_db, arch_fs, priority, model, inherit_id,
             mode, active, website_id, is_origine_view_id,
             create_uid, write_uid, create_date, write_date)
        VALUES 
            (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, 1, 1, NOW(), NOW())
        RETURNING id
    """
    cr_dst.execute(SQL, [
        view['name'], 
        key,
        view['type'], 
        arch_jsonb,
        view['arch_fs'],
        view['priority'] or 16, 
        view['model'],
        new_inherit_id,
        view['mode'] or 'primary', 
        view['active'], 
        website_id,
        old_view_id  # is_origine_view_id = ancien ID dans Odoo 13
    ])
    new_view_id = cr_dst.fetchone()['id']
    print(f"   Vue créée: key={key}, website_id={website_id}, ancien_id={old_view_id} -> nouveau_id={new_view_id}")
    
    view_id_mapping[old_view_id] = new_view_id

cnx_dst.commit()
print(f"\n   Mapping view_id créé: {view_id_mapping}")


# Étape 4: Migrer website_page
print("\n4. Migration de website_page...")
tables=[
    "website_page",
]
for table in tables:
    print(table)
    MigrationTable(db_src,db_dst,table)

# Étape 5: Mettre à jour les view_id dans website_page avec le mapping
print("\n5. Mise à jour des view_id dans website_page...")
for old_view_id, new_view_id in view_id_mapping.items():
    SQL = "UPDATE website_page SET view_id = %s WHERE view_id = %s"
    cr_dst.execute(SQL, [new_view_id, old_view_id])
    if cr_dst.rowcount > 0:
        print(f"   view_id {old_view_id} -> {new_view_id} ({cr_dst.rowcount} page(s) mise(s) à jour)")

# Étape 5b: Définir header_visible et footer_visible à true (colonnes nouvelles dans Odoo 19)
print("\n5b. Activation de header_visible et footer_visible sur toutes les pages migrées...")
SQL = "UPDATE website_page SET header_visible = true, footer_visible = true WHERE header_visible IS NULL OR footer_visible IS NULL"
cr_dst.execute(SQL)
print(f"   {cr_dst.rowcount} page(s) mise(s) à jour")

# Étape 5c: Créer les templates système manquants d'Odoo 13
print("\n5c. Création des templates système manquants d'Odoo 13...")

# Liste des templates système à migrer (qui existent dans Odoo 13 mais pas dans Odoo 19)
templates_systeme = [
    "website.company_description",
]

for template_key in templates_systeme:
    # Vérifier si le template existe déjà dans Odoo 19
    SQL = "SELECT id FROM ir_ui_view WHERE key = %s"
    cr_dst.execute(SQL, [template_key])
    if cr_dst.fetchone():
        print(f"   Template {template_key} existe déjà dans Odoo 19")
        continue
    
    # Récupérer le template depuis Odoo 13
    SQL = "SELECT id, name, key, type, arch_db, arch_fs, priority, model, inherit_id, mode, active FROM ir_ui_view WHERE key = %s"
    cr_src.execute(SQL, [template_key])
    template = cr_src.fetchone()
    
    if not template:
        print(f"   Template {template_key} non trouvé dans Odoo 13")
        continue
    
    # Convertir arch_db en JSONB
    arch_db_value = template['arch_db']
    if arch_db_value:
        arch_str = arch_db_value if isinstance(arch_db_value, str) else str(arch_db_value)
        # Mettre à jour le t-name pour correspondre à la clé
        import re
        old_tname_match = re.search(r't-name=["\']([^"\']+)["\']', arch_str)
        if old_tname_match:
            old_tname = old_tname_match.group(1)
            if old_tname != template_key:
                arch_str = re.sub(r't-name=["\'][^"\']+["\']', f't-name="{template_key}"', arch_str, count=1)
        arch_jsonb = json.dumps({"fr_FR": arch_str})
    else:
        arch_jsonb = None
    
    # Insérer le template dans Odoo 19
    SQL = """
        INSERT INTO ir_ui_view 
            (name, key, type, arch_db, arch_fs, priority, model, inherit_id,
             mode, active, website_id, is_origine_view_id,
             create_uid, write_uid, create_date, write_date)
        VALUES 
            (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, NULL, %s, 1, 1, NOW(), NOW())
        RETURNING id
    """
    cr_dst.execute(SQL, [
        template['name'],
        template_key,
        template['type'],
        arch_jsonb,
        template['arch_fs'],
        template['priority'] or 16,
        template['model'],
        None,  # pas d'inherit_id pour ces templates
        template['mode'] or 'primary',
        template['active'],
        template['id']  # is_origine_view_id
    ])
    new_id = cr_dst.fetchone()['id']
    print(f"   Template {template_key} créé avec id={new_id}")

cnx_dst.commit()

# Étape 5d: Supprimer les vues d'héritage/extension spécifiques au site
# Ces vues (footer, header, etc.) seront gérées par le module is_infosaone19
# On les supprime pour qu'Odoo utilise les vues par défaut modifiées par le module
print("\n5d. Suppression des vues d'héritage migrées (gérées par le module is_infosaone19)...")

# Liste des clés de vues d'héritage à supprimer (celles définies dans le module)
vues_gerees_par_module = [
    "website.footer_custom",
    # Ajouter d'autres clés si nécessaire
]

for vue_key in vues_gerees_par_module:
    SQL = "DELETE FROM ir_ui_view WHERE key = %s AND website_id IS NOT NULL AND is_origine_view_id IS NOT NULL"
    cr_dst.execute(SQL, [vue_key])
    if cr_dst.rowcount > 0:
        print(f"   Vue {vue_key} (website_id spécifique) supprimée")

cnx_dst.commit()

# Étape 6: Migrer website_menu
print("\n6. Migration de website_menu...")

# Supprimer les menus existants dans Odoo 19
SQL = "DELETE FROM website_menu"
cr_dst.execute(SQL)
print(f"   {cr_dst.rowcount} menus supprimés dans Odoo 19")
cnx_dst.commit()

# Migrer les menus d'Odoo 13
MigrationTable(db_src, db_dst, "website_menu", text2jsonb=True)
print("   website_menu migré")

print("\n" + "="*70)
print("Migration des vues, website_page et website_menu terminée")
print("="*70)
#******************************************************************************

# # Récupérer les website_page d'Odoo 13
# SQL = "SELECT * FROM website_page"
# cr_src.execute(SQL)
# pages_src = cr_src.fetchall()

# # Récupérer les colonnes de website_page dans Odoo 19
# colonnes_dst = GetChamps(cr_dst, "website_page")
# colonnes_src = GetChamps(cr_src, "website_page")

# # Colonnes communes (exclure celles qui n'existent pas dans la destination)
# colonnes_communes = [c for c in colonnes_src if c in colonnes_dst and c != 'id']

# print(f"   Colonnes à migrer: {colonnes_communes}")

# for page in pages_src:
#     old_view_id = page['view_id']
#     new_view_id = view_id_mapping.get(old_view_id)
    
#     if new_view_id:
#         # Construire la requête INSERT
#         cols = ['id'] + [c for c in colonnes_communes if c != 'view_id'] + ['view_id']
#         vals = [page['id']] + [page[c.strip('"')] for c in colonnes_communes if c != 'view_id'] + [new_view_id]
        
#         placeholders = ', '.join(['%s'] * len(vals))
#         cols_str = ', '.join(cols)
        
#         SQL = f"INSERT INTO website_page ({cols_str}) VALUES ({placeholders})"
#         try:
#             cr_dst.execute(SQL, vals)
#             print(f"   Page migrée: id={page['id']}, url={page['url']}, view_id: {old_view_id} -> {new_view_id}")
#         except Exception as e:
#             print(f"   ERREUR page id={page['id']}: {e}")
#     else:
#         print(f"   ATTENTION: Pas de mapping pour view_id={old_view_id} (page id={page['id']}, url={page['url']})")

# cnx_dst.commit()

# # Réinitialiser la séquence de website_page
# SQL = "SELECT setval('website_page_id_seq', (SELECT COALESCE(MAX(id), 1) FROM website_page))"
# cr_dst.execute(SQL)
# cnx_dst.commit()

# print("\n" + "="*70)
# print("Migration des vues et website_page terminée")
# print("="*70)
# #******************************************************************************





































# # ** Correction des website_page - Mapping view_id Odoo 13 -> Odoo 19 *********
# # Les view_id ont changé entre les versions, on doit les remapper via la clé (key)
# print("Correction des view_id dans website_page...")

# # Mapping basé sur ir_ui_view.key : ancien view_id -> nouveau view_id
# # On récupère les nouveaux view_id depuis Odoo 19 par leur key
# view_mapping = {
#     'website.homepage': None,
#     'website.contactus': None,
#     'website.aboutus': None,
#     'website.contactus_thanks': None,
# }

# # Récupérer les view_id d'Odoo 19 par key
# for key in view_mapping.keys():
#     SQL = "SELECT id FROM ir_ui_view WHERE key = %s AND type = 'qweb' LIMIT 1"
#     cr_dst.execute(SQL, [key])
#     result = cr_dst.fetchone()
#     if result:
#         view_mapping[key] = result['id']

# print(f"View mapping: {view_mapping}")

# # Récupérer le mapping ancien view_id -> key depuis Odoo 13
# old_view_to_key = {}
# SQL = """
#     SELECT DISTINCT v.id, v.key 
#     FROM ir_ui_view v
#     JOIN website_page wp ON wp.view_id = v.id
#     WHERE v.key IS NOT NULL
# """
# cr_src.execute(SQL)
# for row in cr_src.fetchall():
#     old_view_to_key[row['id']] = row['key']

# print(f"Old view_id to key: {old_view_to_key}")

# # Mettre à jour les view_id dans website_page d'Odoo 19
# for old_view_id, key in old_view_to_key.items():
#     if key in view_mapping and view_mapping[key]:
#         new_view_id = view_mapping[key]
#         SQL = "UPDATE website_page SET view_id = %s WHERE view_id = %s"
#         cr_dst.execute(SQL, [new_view_id, old_view_id])
#         print(f"  view_id {old_view_id} ({key}) -> {new_view_id}")

# cnx_dst.commit()

# # ** Migration des vues QWeb personnalisées (pages du site web) ***************
# # Les vues avec website_id sont les personnalisations spécifiques au site
# print("\nMigration des vues QWeb personnalisées...")

# import json

# # Récupérer les vues QWeb personnalisées depuis Odoo 13
# SQL = """
#     SELECT v.id, v.name, v.key, v.type, v.arch_db, v.priority, v.mode, v.active,
#            v.website_id, v.inherit_id, v.customize_show, v.track,
#            v.website_meta_title, v.website_meta_description, v.website_meta_keywords
#     FROM ir_ui_view v
#     JOIN website_page wp ON wp.view_id = v.id
#     WHERE v.website_id IS NOT NULL
# """
# cr_src.execute(SQL)
# custom_views = cr_src.fetchall()

# for view in custom_views:
#     old_view_id = view['id']
#     key = view['key']
    
#     # Vérifier si une vue avec cette clé existe déjà dans Odoo 19 pour ce website
#     SQL = "SELECT id FROM ir_ui_view WHERE key = %s AND website_id = %s"
#     cr_dst.execute(SQL, [key, view['website_id']])
#     existing = cr_dst.fetchone()
    
#     # Trouver le inherit_id correspondant dans Odoo 19 (via la clé de la vue parente)
#     new_inherit_id = None
#     if view['inherit_id']:
#         SQL = "SELECT key FROM ir_ui_view WHERE id = %s"
#         cr_src.execute(SQL, [view['inherit_id']])
#         parent = cr_src.fetchone()
#         if parent and parent['key']:
#             SQL = "SELECT id FROM ir_ui_view WHERE key = %s AND website_id IS NULL LIMIT 1"
#             cr_dst.execute(SQL, [parent['key']])
#             new_parent = cr_dst.fetchone()
#             if new_parent:
#                 new_inherit_id = new_parent['id']
    
#     # Convertir arch_db en JSONB (format Odoo 19)
#     arch_jsonb = json.dumps({"en_US": view['arch_db']}) if view['arch_db'] else None
    
#     # Convertir les meta en JSONB
#     meta_title = json.dumps({"en_US": view['website_meta_title']}) if view['website_meta_title'] else None
#     meta_desc = json.dumps({"en_US": view['website_meta_description']}) if view['website_meta_description'] else None
#     meta_keywords = json.dumps({"en_US": view['website_meta_keywords']}) if view['website_meta_keywords'] else None
    
#     if existing:
#         # Mettre à jour la vue existante
#         SQL = """
#             UPDATE ir_ui_view 
#             SET arch_db = %s::jsonb,
#                 website_meta_title = %s::jsonb,
#                 website_meta_description = %s::jsonb,
#                 website_meta_keywords = %s::jsonb,
#                 active = %s
#             WHERE id = %s
#         """
#         cr_dst.execute(SQL, [arch_jsonb, meta_title, meta_desc, meta_keywords, view['active'], existing['id']])
#         new_view_id = existing['id']
#         print(f"  Vue mise à jour: {key} (id={new_view_id})")
#     else:
#         # Insérer une nouvelle vue
#         SQL = """
#             INSERT INTO ir_ui_view 
#                 (name, key, type, arch_db, priority, mode, active, website_id, inherit_id, 
#                  customize_show, track, website_meta_title, website_meta_description, 
#                  website_meta_keywords, create_uid, write_uid, create_date, write_date)
#             VALUES 
#                 (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, 1, 1, NOW(), NOW())
#             RETURNING id
#         """
#         cr_dst.execute(SQL, [
#             view['name'], key, view['type'], arch_jsonb, view['priority'] or 16, 
#             view['mode'] or 'primary', view['active'], view['website_id'], new_inherit_id,
#             view['customize_show'], view['track'], meta_title, meta_desc, meta_keywords
#         ])
#         new_view_id = cr_dst.fetchone()['id']
#         print(f"  Vue créée: {key} (id={new_view_id})")
    
#     # Mettre à jour website_page avec le nouveau view_id
#     SQL = "UPDATE website_page SET view_id = %s WHERE view_id = %s"
#     cr_dst.execute(SQL, [new_view_id, old_view_id])

# cnx_dst.commit()
# print("Migration des vues QWeb terminée")
# #******************************************************************************

# Fin du script
print("\n" + "="*70)
print("MIGRATION TERMINÉE")
print("="*70)


