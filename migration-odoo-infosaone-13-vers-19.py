#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from migration_fonction import *
import os
import json


#TODO
# - res.company
# - ir_attachment
# - Il faudra mettre en place une redirection pour la page contact pour Google car son url a changé
# - Voir pourquou j'ai des menu en double dans website_menu


#** Paramètres ****************************************************************
db_src = "infosaone13"
db_dst = "infosaone19"
#******************************************************************************


#** Permet de repartir sur une base vierge si la migration échoue *************
db_vierge = db_dst+'-vierge'
SQL='DROP DATABASE \"'+db_dst+'\";CREATE DATABASE \"'+db_dst+'\" WITH TEMPLATE \"'+db_vierge+'\"'
cde="""echo '"""+SQL+"""' | psql postgres"""
lines=os.popen(cde).readlines() #Permet de repartir sur une base vierge si la migration échoue
# rsync -rva --delete /media/sf_dev_odoo/home/odoo/filestore/infosaone19-vierge/ /media/sf_dev_odoo/home/odoo/filestore/infosaone19
#******************************************************************************



cnx_src,cr_src=GetCR(db_src)
cnx_dst,cr_dst=GetCR(db_dst)


# ** Tables diverses **********************************************************
tables=[
    "website",
    "website_lang_rel",
    "website_page",   # Migré plus tard après la gestion des vues (à cause des page_id)
    #"website_track",   # Actvier plus tard pour ganger du temps de traitement
    #"website_visitor", # Actvier plus tard pour ganger du temps de traitement
    "blog_blog",
    "blog_post",
    "blog_tag",
    "blog_tag_category",
    "blog_post_blog_tag_rel",
]
for table in tables:
    print(table)
    MigrationTable(db_src,db_dst,table,text2jsonb=True)
#*******************************************************************************


# ** Correction des IDs de langue (fr_FR: 27 dans v13 -> 30 dans v19) *********
SQL = """
    UPDATE website_lang_rel 
    SET lang_id = (SELECT id FROM res_lang WHERE code = 'fr_FR');

    UPDATE website 
    SET default_lang_id = (SELECT id FROM res_lang WHERE code = 'fr_FR');

    update website_page 
    set header_visible=true, footer_visible=true, header_overlay=Null;

    update blog_post 
    set header_visible=true, footer_visible=true
"""
cr_dst.execute(SQL)
cnx_dst.commit()
#******************************************************************************


# ** Migration des pages website_page avec website_id=1 ***********************
print("\n" + "="*70)
print("Migration des pages website_page (website_id=1)")
print("="*70)

# Récupérer les pages avec website_id=1 dans Odoo 13
SQL = """
    SELECT id, website_id, view_id, url 
    FROM website_page 
    WHERE website_id = 1
"""
cr_src.execute(SQL)
pages_src = cr_src.fetchall()
print(f"Trouvé {len(pages_src)} page(s) avec website_id=1 dans Odoo 13")

# Mapping ancien view_id -> nouveau view_id
page_view_mapping = {}

# Pour chaque page, copier la vue d'Odoo 13 vers Odoo 19
for page in pages_src:
    old_view_id = page['view_id']
    
    # Récupérer la vue correspondante dans Odoo 13
    SQL = """
        SELECT id, name, key, type, arch_db, arch_fs, priority, model, 
               inherit_id, mode, active, website_id
        FROM ir_ui_view 
        WHERE id = %s
    """
    cr_src.execute(SQL, [old_view_id])
    view = cr_src.fetchone()
    
    if not view:
        print(f"   ERREUR: Vue {old_view_id} non trouvée pour page {page['url']}")
        continue
    
    # Convertir arch_db en JSONB (format Odoo 19)
    arch_db_value = view['arch_db']
    if arch_db_value and str(arch_db_value).strip():
        arch_str = arch_db_value if isinstance(arch_db_value, str) else str(arch_db_value)
        arch_str = arch_str.strip()
        
        # Vérifier que c'est bien du XML valide
        if not arch_str.startswith('<'):
            print(f"   ERREUR: arch_db invalide pour vue {old_view_id} ({view['key']})")
            continue
        
        import re
        # Mettre à jour le t-name pour correspondre à la clé de la vue
        key = view['key']
        old_tname_match = re.search(r't-name=["\']([^"\']+)["\']', arch_str)
        if old_tname_match:
            old_tname = old_tname_match.group(1)
            if old_tname != key:
                arch_str = re.sub(
                    r't-name=["\'][^"\']+["\']',
                    f't-name="{key}"',
                    arch_str,
                    count=1
                )
        
        # Utiliser en_US comme clé principale (standard Odoo)
        arch_jsonb = json.dumps({"en_US": arch_str})
    else:
        print(f"   ERREUR: arch_db vide pour vue {old_view_id} ({view['key']})")
        continue
    
    # Vérifier si la vue existe déjà dans Odoo 19
    SQL = "SELECT id FROM ir_ui_view WHERE key = %s AND website_id = %s"
    cr_dst.execute(SQL, [view['key'], view['website_id']])
    existing_view = cr_dst.fetchone()
    
    if existing_view:
        # Mettre à jour la vue existante
        new_view_id = existing_view['id']
        SQL = """
            UPDATE ir_ui_view 
            SET arch_db = %s::jsonb,
                is_origine_view_id = %s,
                write_date = NOW()
            WHERE id = %s
        """
        cr_dst.execute(SQL, [arch_jsonb, old_view_id, new_view_id])
        print(f"   MAJ vue: {view['key']} (url={page['url']})")
    else:
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
            view['key'],
            view['type'],
            arch_jsonb,
            view['arch_fs'],
            view['priority'] or 16,
            view['model'],
            view['inherit_id'],
            view['mode'] or 'primary',
            view['active'],
            view['website_id'],
            old_view_id
        ])
        new_view_id = cr_dst.fetchone()['id']
        print(f"   Créée vue: {view['key']} (url={page['url']})")
    
    page_view_mapping[old_view_id] = new_view_id

cnx_dst.commit()
print(f"Migration de {len(page_view_mapping)} vue(s) terminée")

# Mettre à jour les view_id dans website_page pour les pages avec website_id=1
print("Mise à jour des view_id dans website_page...")
for old_view_id, new_view_id in page_view_mapping.items():
    SQL = "UPDATE website_page SET view_id = %s WHERE view_id = %s AND website_id = 1"
    cr_dst.execute(SQL, [new_view_id, old_view_id])

cnx_dst.commit()
print("Mise à jour terminée")
#******************************************************************************


# ** Migration de website_menu *************************************************
MigrationTable(db_src, db_dst, "website_menu", text2jsonb=True)


# Mapping via URL (correspondance entre pages source et destination)
page_id_mapping = {}
SQL = "SELECT id, url FROM website_page WHERE website_id = 1"
cr_src.execute(SQL)
pages_src = {row['url']: row['id'] for row in cr_src.fetchall()}

SQL = "SELECT id, url FROM website_page WHERE website_id = 1"
cr_dst.execute(SQL)
pages_dst = {row['url']: row['id'] for row in cr_dst.fetchall()}

for url, old_id in pages_src.items():
    if url in pages_dst:
        page_id_mapping[old_id] = pages_dst[url]


# Mettre à jour les page_id dans website_menu
updates = 0
for old_page_id, new_page_id in page_id_mapping.items():
    SQL = "UPDATE website_menu SET page_id = %s WHERE page_id = %s AND website_id = 1"
    cr_dst.execute(SQL, [new_page_id, old_page_id])
    updates += cr_dst.rowcount

cnx_dst.commit()
print(f"{updates} page_id mis à jour dans website_menu")
print("Migration website_menu terminée")
#******************************************************************************



#** La page de contact est dans le module maintenant **************************
SQL = """
    delete from website_menu where url='/contact';
"""
cr_dst.execute(SQL)
cnx_dst.commit()
#******************************************************************************




sys.exit()





# # ** Correction des IDs de langue (fr_FR: 27 dans v13 -> 30 dans v19) *********
# SQL = """
#     UPDATE website 
#     SET default_lang_id = (SELECT id FROM res_lang WHERE code = 'fr_FR')
#     WHERE default_lang_id IS NOT NULL;
# """
# cr_dst.execute(SQL)
# cnx_dst.commit()

# SQL = """
#     UPDATE website_lang_rel 
#     SET lang_id = (SELECT id FROM res_lang WHERE code = 'fr_FR')
#     WHERE lang_id NOT IN (SELECT id FROM res_lang);
# """
# cr_dst.execute(SQL)
# cnx_dst.commit()
# #******************************************************************************


# # ** Migration des vues ir_ui_view liées aux website_page ET vues personnalisées **
# print("\n" + "="*70)
# print("Migration des vues ir_ui_view (pages + vues personnalisées du site)")
# print("="*70)

# # Nettoyage préalable: supprimer les pages dupliquées qui ne viennent pas du module
# # Cela évite les conflits lors de la mise à jour du module après la migration
# print("0. Nettoyage des pages dupliquées...")
# SQL = """
#     -- Supprimer les pages avec URL /contact qui ne viennent PAS du module is_infosaone19
#     DELETE FROM website_page 
#     WHERE url = '/contact' 
#     AND id NOT IN (
#         SELECT res_id FROM ir_model_data 
#         WHERE model = 'website.page' 
#         AND module = 'is_infosaone19'
#     );
# """
# cr_dst.execute(SQL)
# deleted_pages = cr_dst.rowcount
# if deleted_pages:
#     print(f"   {deleted_pages} page(s) /contact dupliquée(s) supprimée(s)")
# cnx_dst.commit()

# # Étape 1: Récupérer TOUTES les vues avec website_id dans Odoo 13
# # (inclut les vues de pages ET les vues personnalisées comme footer, header, blog, etc.)
# SQL = """
#     SELECT DISTINCT v.id, v.name, v.key, v.type, v.arch_fs, 
#            v.priority, v.model, v.inherit_id,
#            v.mode, v.active, v.website_id
#     FROM ir_ui_view v
#     WHERE v.website_id IS NOT NULL
# """
# cr_src.execute(SQL)
# views_src = cr_src.fetchall()
# print(f"1. {len(views_src)} vues personnalisées trouvées dans Odoo 13")

# # Étape 2: Supprimer les website_page et vues MIGRÉES dans Odoo 19
# # IMPORTANT: Ne PAS supprimer les vues qui viennent de modules installés (ir_model_data)

# # Récupérer les IDs des vues qui proviennent de modules (à ne PAS supprimer)
# SQL = """
#     SELECT res_id FROM ir_model_data 
#     WHERE model = 'ir.ui.view' AND module != '__export__'
# """
# cr_dst.execute(SQL)
# module_view_ids = {row['res_id'] for row in cr_dst.fetchall()}
# print(f"   {len(module_view_ids)} vues provenant de modules (protégées)")

# # Récupérer les view_id des pages existantes (sauf celles de modules)
# SQL = "SELECT DISTINCT view_id FROM website_page WHERE view_id IS NOT NULL"
# cr_dst.execute(SQL)
# view_ids_to_delete = [row['view_id'] for row in cr_dst.fetchall() if row['view_id'] not in module_view_ids]

# # Récupérer aussi les vues créées par des migrations précédentes (is_origine_view_id IS NOT NULL)
# SQL = "SELECT id FROM ir_ui_view WHERE is_origine_view_id IS NOT NULL"
# cr_dst.execute(SQL)
# view_ids_migrated = [row['id'] for row in cr_dst.fetchall() if row['id'] not in module_view_ids]

# # Fusionner les listes (SANS les vues personnalisées avec website_id qui peuvent venir de modules)
# all_view_ids_to_delete = list(set(view_ids_to_delete + view_ids_migrated))
# print(f"2. Suppression de {len(all_view_ids_to_delete)} vues migrées dans Odoo 19")

# # Supprimer les website_page qui ne viennent pas de modules
# if module_view_ids:
#     SQL = "DELETE FROM website_page WHERE view_id NOT IN %s OR view_id IS NULL"
#     cr_dst.execute(SQL, (tuple(module_view_ids),))
# else:
#     SQL = "DELETE FROM website_page"
#     cr_dst.execute(SQL)

# # Supprimer les vues migrées (PAS celles des modules)
# if all_view_ids_to_delete:
#     SQL = "DELETE FROM ir_ui_view WHERE id IN %s"
#     cr_dst.execute(SQL, (tuple(all_view_ids_to_delete),))

# cnx_dst.commit()

# # Étape 3: Insérer les vues d'Odoo 13 dans Odoo 19
# print("3. Insertion des vues d'Odoo 13 dans Odoo 19...")

# # Trier les vues pour que les vues parentes soient créées avant les vues enfants
# # Les vues sans inherit_id d'abord, puis celles avec inherit_id
# def get_view_order(views):
#     """Trie les vues pour respecter les dépendances inherit_id"""
#     view_ids = {v['id'] for v in views}
#     ordered = []
#     remaining = list(views)
#     processed_ids = set()
    
#     # Maximum 10 passes pour éviter les boucles infinies
#     for _ in range(10):
#         if not remaining:
#             break
#         still_remaining = []
#         for view in remaining:
#             # Vue sans inherit_id ou inherit_id hors de notre liste ou déjà traité
#             if not view['inherit_id'] or view['inherit_id'] not in view_ids or view['inherit_id'] in processed_ids:
#                 ordered.append(view)
#                 processed_ids.add(view['id'])
#             else:
#                 still_remaining.append(view)
#         remaining = still_remaining
    
#     # Ajouter les vues restantes (dépendances non résolues)
#     ordered.extend(remaining)
#     return ordered

# views_src = get_view_order(views_src)

# # Mapping ancien view_id -> nouveau view_id
# view_id_mapping = {}

# # Vérifier quelles vues sont liées à des website_page (ce sont les vraies "pages")
# SQL = "SELECT DISTINCT view_id FROM website_page WHERE view_id IS NOT NULL"
# cr_src.execute(SQL)
# page_view_ids = {row['view_id'] for row in cr_src.fetchall()}

# # Liste des clés de vues à NE PAS migrer (gérées par le module is_infosaone19)
# vues_gerees_par_module = [
#     "website.contactus",  # Page de contact gérée par is_infosaone19
# ]

# for view in views_src:
#     old_view_id = view['id']
#     website_id = view['website_id']
#     key = view['key']
#     original_key = key
    
#     # Ignorer les vues gérées par le module is_infosaone19
#     if original_key in vues_gerees_par_module:
#         print(f"   IGNORÉE (module): {original_key}")
#         continue
    
#     # Seules les vues liées à une website_page doivent avoir leur clé modifiée
#     # Les templates de rendu (blog, layout, etc.) doivent garder leur clé originale
#     # pour que les t-call fonctionnent et pour qu'Odoo sélectionne la vue spécifique au website
#     if website_id and key and view['mode'] == 'primary' and not view['inherit_id']:
#         if old_view_id in page_view_ids:
#             # C'est une page du site, on modifie la clé
#             key = f"{key}_migrated_{old_view_id}"
#             print(f"   PAGE: {original_key} -> {key}")
#         else:
#             # C'est un template de rendu, on garde la clé originale
#             print(f"   TEMPLATE CONSERVÉ: {key} (mode={view['mode']})")
    
#     # Trouver le inherit_id correspondant dans Odoo 19
#     new_inherit_id = None
#     if view['inherit_id']:
#         old_inherit_id = view['inherit_id']
        
#         # D'abord vérifier si la vue parente a déjà été migrée (dans notre mapping)
#         if old_inherit_id in view_id_mapping:
#             new_inherit_id = view_id_mapping[old_inherit_id]
#         else:
#             # Sinon chercher par clé dans Odoo 19
#             SQL = "SELECT key FROM ir_ui_view WHERE id = %s"
#             cr_src.execute(SQL, [old_inherit_id])
#             parent = cr_src.fetchone()
#             if parent and parent['key']:
#                 SQL = "SELECT id FROM ir_ui_view WHERE key = %s LIMIT 1"
#                 cr_dst.execute(SQL, [parent['key']])
#                 new_parent = cr_dst.fetchone()
#                 if new_parent:
#                     new_inherit_id = new_parent['id']
        
#         # Si inherit_id non résolu et mode=extension, on ne peut pas créer la vue
#         if new_inherit_id is None and view['mode'] == 'extension':
#             print(f"   IGNORÉE: {key} - inherit_id non résolu")
#             continue
    
#     # Récupérer arch_db séparément (pour éviter les problèmes avec DISTINCT sur TEXT)
#     SQL = "SELECT arch_db FROM ir_ui_view WHERE id = %s"
#     cr_src.execute(SQL, [old_view_id])
#     result = cr_src.fetchone()
#     arch_db_value = result['arch_db'] if result else None
    
#     # Convertir arch_db en JSONB (format Odoo 19)
#     # Dans Odoo 13, arch_db est du texte brut, dans Odoo 19 c'est du JSONB
#     arch_jsonb = None
#     if arch_db_value and str(arch_db_value).strip():
#         arch_str = arch_db_value if isinstance(arch_db_value, str) else str(arch_db_value)
#         arch_str = arch_str.strip()
        
#         # Vérifier que c'est bien du XML valide (doit commencer par <)
#         if not arch_str.startswith('<'):
#             print(f"   IGNORÉE: arch_db invalide pour vue {old_view_id} ({key}) - ne commence pas par '<'")
#             continue
        
#         import re
#         # Toujours mettre à jour le t-name pour qu'il corresponde à la clé de la vue
#         # Le t-name dans le XML peut être différent de la clé (ex: website.homepage1 vs website.homepage)
#         # On remplace le premier t-name trouvé par la nouvelle clé
#         old_tname_match = re.search(r't-name=["\']([^"\']+)["\']', arch_str)
#         if old_tname_match:
#             old_tname = old_tname_match.group(1)
#             if old_tname != key:
#                 arch_str = re.sub(
#                     r't-name=["\'][^"\']+["\']',
#                     f't-name="{key}"',
#                     arch_str,
#                     count=1  # Remplacer seulement le premier
#                 )
        
#         # Utiliser en_US comme clé principale (standard Odoo)
#         arch_jsonb = json.dumps({"en_US": arch_str})
#     else:
#         print(f"   IGNORÉE: arch_db vide pour vue {old_view_id} ({key})")
#         continue
    
#     # Insérer la nouvelle vue
#     SQL = """
#         INSERT INTO ir_ui_view 
#             (name, key, type, arch_db, arch_fs, priority, model, inherit_id,
#              mode, active, website_id, is_origine_view_id,
#              create_uid, write_uid, create_date, write_date)
#         VALUES 
#             (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, 1, 1, NOW(), NOW())
#         RETURNING id
#     """
#     cr_dst.execute(SQL, [
#         view['name'], 
#         key,
#         view['type'], 
#         arch_jsonb,
#         view['arch_fs'],
#         view['priority'] or 16, 
#         view['model'],
#         new_inherit_id,
#         view['mode'] or 'primary', 
#         view['active'], 
#         website_id,
#         old_view_id  # is_origine_view_id = ancien ID dans Odoo 13
#     ])
#     new_view_id = cr_dst.fetchone()['id']
    
#     view_id_mapping[old_view_id] = new_view_id

# cnx_dst.commit()
# print(f"   {len(view_id_mapping)} vues créées")


# # Étape 4: Migrer website_page
# print("4. Migration de website_page...")
# tables=[
#     "website_page",
# ]
# for table in tables:
#     MigrationTable(db_src,db_dst,table)

# # Étape 5: Mettre à jour les view_id dans website_page avec le mapping
# for old_view_id, new_view_id in view_id_mapping.items():
#     SQL = "UPDATE website_page SET view_id = %s WHERE view_id = %s"
#     cr_dst.execute(SQL, [new_view_id, old_view_id])

# # Étape 5b: Définir header_visible et footer_visible à true (colonnes nouvelles dans Odoo 19)
# SQL = "UPDATE website_page SET header_visible = true, footer_visible = true WHERE header_visible IS NULL OR footer_visible IS NULL"
# cr_dst.execute(SQL)
# print(f"5. Mise à jour des website_page: header_visible et footer_visible activés")

# # Étape 5c: Créer les templates système manquants d'Odoo 13

# # Liste des templates système à migrer (qui existent dans Odoo 13 mais pas dans Odoo 19)
# templates_systeme = [
#     "website.company_description",
# ]

# for template_key in templates_systeme:
#     # Vérifier si le template existe déjà dans Odoo 19
#     SQL = "SELECT id FROM ir_ui_view WHERE key = %s"
#     cr_dst.execute(SQL, [template_key])
#     if cr_dst.fetchone():
#         continue
    
#     # Récupérer le template depuis Odoo 13
#     SQL = "SELECT id, name, key, type, arch_db, arch_fs, priority, model, inherit_id, mode, active FROM ir_ui_view WHERE key = %s"
#     cr_src.execute(SQL, [template_key])
#     template = cr_src.fetchone()
    
#     if not template:
#         continue
    
#     # Convertir arch_db en JSONB
#     arch_db_value = template['arch_db']
#     if arch_db_value and str(arch_db_value).strip():
#         arch_str = arch_db_value if isinstance(arch_db_value, str) else str(arch_db_value)
#         arch_str = arch_str.strip()
        
#         # Vérifier que c'est bien du XML valide
#         if not arch_str.startswith('<'):
#             print(f"   IGNORÉ: template {template_key} - arch_db invalide")
#             continue
            
#         # Mettre à jour le t-name pour correspondre à la clé
#         import re
#         old_tname_match = re.search(r't-name=["\']([^"\']+)["\']', arch_str)
#         if old_tname_match:
#             old_tname = old_tname_match.group(1)
#             if old_tname != template_key:
#                 arch_str = re.sub(r't-name=["\'][^"\']+["\']', f't-name="{template_key}"', arch_str, count=1)
#         arch_jsonb = json.dumps({"en_US": arch_str})
#     else:
#         print(f"   IGNORÉ: template {template_key} - arch_db vide")
#         continue
    
#     # Insérer le template dans Odoo 19
#     SQL = """
#         INSERT INTO ir_ui_view 
#             (name, key, type, arch_db, arch_fs, priority, model, inherit_id,
#              mode, active, website_id, is_origine_view_id,
#              create_uid, write_uid, create_date, write_date)
#         VALUES 
#             (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, NULL, %s, 1, 1, NOW(), NOW())
#         RETURNING id
#     """
#     cr_dst.execute(SQL, [
#         template['name'],
#         template_key,
#         template['type'],
#         arch_jsonb,
#         template['arch_fs'],
#         template['priority'] or 16,
#         template['model'],
#         None,  # pas d'inherit_id pour ces templates
#         template['mode'] or 'primary',
#         template['active'],
#         template['id']  # is_origine_view_id
#     ])

# cnx_dst.commit()

# # Étape 5d: Supprimer les vues d'héritage/extension spécifiques au site
# # Ces vues (footer, header, etc.) seront gérées par le module is_infosaone19
# # On les supprime pour qu'Odoo utilise les vues par défaut modifiées par le module

# # Liste des clés de vues d'héritage à supprimer (celles définies dans le module)
# vues_gerees_par_module = [
#     "website.footer_custom",
#     # Ajouter d'autres clés si nécessaire
# ]

# for vue_key in vues_gerees_par_module:
#     SQL = "DELETE FROM ir_ui_view WHERE key = %s AND website_id IS NOT NULL AND is_origine_view_id IS NOT NULL"
#     cr_dst.execute(SQL, [vue_key])

# cnx_dst.commit()

# # Étape 6: Migrer website_menu
# print("6. Migration de website_menu...")

# # Supprimer les menus existants dans Odoo 19
# SQL = "DELETE FROM website_menu"
# cr_dst.execute(SQL)
# cnx_dst.commit()

# # Migrer les menus d'Odoo 13
# MigrationTable(db_src, db_dst, "website_menu", text2jsonb=True)

# # Mettre à jour les page_id dans website_menu avec les nouveaux IDs des pages
# print("   Mise à jour des page_id dans website_menu...")

# # Créer un mapping old_page_id -> new_page_id via l'URL
# SQL = """
#     SELECT p_src.id as old_id, p_dst.id as new_id
#     FROM website_page p_dst
#     WHERE p_dst.website_id = 1
#     AND p_dst.is_origine_page_id IS NOT NULL
# """
# cr_dst.execute(SQL)
# page_mapping_results = cr_dst.fetchall()

# # Si is_origine_page_id n'existe pas, utiliser une autre méthode
# if not page_mapping_results:
#     # Mapping via URL (correspondance entre pages source et destination)
#     page_id_mapping = {}
#     SQL = "SELECT id, url FROM website_page WHERE website_id = 1"
#     cr_src.execute(SQL)
#     pages_src = {row['url']: row['id'] for row in cr_src.fetchall()}
    
#     SQL = "SELECT id, url FROM website_page WHERE website_id = 1"
#     cr_dst.execute(SQL)
#     pages_dst = {row['url']: row['id'] for row in cr_dst.fetchall()}
    
#     for url, old_id in pages_src.items():
#         if url in pages_dst:
#             page_id_mapping[old_id] = pages_dst[url]
# else:
#     # Utiliser is_origine_page_id
#     page_id_mapping = {row['is_origine_page_id']: row['id'] for row in page_mapping_results}

# # Mettre à jour les page_id dans website_menu
# updates = 0
# for old_page_id, new_page_id in page_id_mapping.items():
#     SQL = "UPDATE website_menu SET page_id = %s WHERE page_id = %s AND website_id = 1"
#     cr_dst.execute(SQL, [new_page_id, old_page_id])
#     updates += cr_dst.rowcount

# cnx_dst.commit()
# print(f"   {updates} page_id mis à jour dans website_menu")

# # Étape 7: Corriger header_visible et footer_visible des articles de blog
# # Ces champs n'existaient pas dans Odoo 13 et doivent être à true pour afficher le header/footer
# print("7. Correction header_visible/footer_visible des articles de blog...")
# SQL = """
#     UPDATE blog_post 
#     SET header_visible = true, footer_visible = true 
#     WHERE header_visible IS NULL OR footer_visible IS NULL
# """
# cr_dst.execute(SQL)
# nb_updated = cr_dst.rowcount
# print(f"   {nb_updated} article(s) de blog mis à jour")
# cnx_dst.commit()

# # Vérification finale : afficher les vues blog avec website_id
# print("\n" + "="*70)
# print("VÉRIFICATION : Vues blog")
# print("="*70)
# SQL = """
#     SELECT id, key, website_id, mode, active 
#     FROM ir_ui_view 
#     WHERE key IN ('website_blog.index', 'website_blog.blog_post_complete')
#     ORDER BY key, website_id
# """
# cr_dst.execute(SQL)
# for row in cr_dst.fetchall():
#     print(f"   id={row['id']}, key={row['key']}, website_id={row['website_id']}")

# # Comparer arch_db entre vue migrée et vue par défaut
# print("\n--- Comparaison arch_db website_blog.index ---")
# SQL = """
#     SELECT id, website_id, LEFT(arch_db::text, 500) as arch_debut 
#     FROM ir_ui_view 
#     WHERE key = 'website_blog.index'
#     ORDER BY website_id NULLS FIRST
# """
# cr_dst.execute(SQL)
# for row in cr_dst.fetchall():
#     print(f"\n[website_id={row['website_id']}] id={row['id']}:")
#     print(row['arch_debut'][:300] if row['arch_debut'] else "VIDE")

# print("\n" + "="*70)
# print("Migration terminée")
# print("="*70)
# #******************************************************************************































# # Fin du script
# print("\n" + "="*70)
# print("MIGRATION TERMINÉE")
# print("="*70)








# MigrationDonneesTable(db_src,db_dst,'res_company')

# default={
# #    'autopost_bills': 'ask',
# }
# MigrationTable(db_src,db_dst,'res_partner',text2jsonb=True,default=default)






# #** Pièces jointes ************************************************************
# MigrationTable(db_src,db_dst,'ir_attachment')
# #******************************************************************************

# # Nettoyage des assets - IMPORTANT: exécuter chaque DELETE séparément
# print("\n" + "="*70)
# print("Nettoyage des assets")
# print("="*70)

# # 1. Supprimer les fichiers SCSS/CSS compilés d'Odoo 13
# cr_dst.execute("DELETE FROM ir_attachment WHERE url LIKE '%.scss.css'")
# print(f"   Supprimé {cr_dst.rowcount} fichiers .scss.css")

# # 2. Supprimer les assets compilés /web/assets/
# cr_dst.execute("DELETE FROM ir_attachment WHERE url LIKE '/web/assets/%'")
# print(f"   Supprimé {cr_dst.rowcount} fichiers /web/assets/")

# # # 3. Supprimer les assets /web/content/
# # cr_dst.execute("DELETE FROM ir_attachment WHERE url LIKE '/web/content/%'")
# # print(f"   Supprimé {cr_dst.rowcount} fichiers /web/content/")

# # # 4. Supprimer les scss personnalisés user
# # cr_dst.execute("DELETE FROM ir_attachment WHERE url LIKE '%user_values%' OR url LIKE '%user_theme%'")
# # print(f"   Supprimé {cr_dst.rowcount} fichiers user_values/user_theme")

# # # 5. Supprimer les fichiers _custom
# # cr_dst.execute("DELETE FROM ir_attachment WHERE url LIKE '%_custom%' OR name LIKE '%_custom%'")
# # print(f"   Supprimé {cr_dst.rowcount} fichiers _custom")

# # # 6. Supprimer les fichiers SCSS de res.company (anciens)
# # cr_dst.execute("DELETE FROM ir_attachment WHERE name LIKE 'res.company.scss' OR url LIKE '%asset_styles_company%'")
# # print(f"   Supprimé {cr_dst.rowcount} fichiers res.company.scss")

# # # 7. Supprimer les ir_asset personnalisés
# # cr_dst.execute("DELETE FROM ir_asset WHERE path LIKE '%_custom%' OR path LIKE '/_custom/%'")
# # print(f"   Supprimé {cr_dst.rowcount} ir_asset _custom")

# cnx_dst.commit()
# print("   Nettoyage terminé")


