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



#** res_company ***************************************************************
MigrationDonneesTable(db_src,db_dst,'res_company')
#******************************************************************************





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





#** Pièces jointes ************************************************************
#print("Migration ir_attachment")
#MigrationTable(db_src,db_dst,'ir_attachment')

print("\n" + "="*70)
print("Migration ir_attachment (INSERT INTO sélectif)")
print("="*70)

# Lister les colonnes disponibles dans Odoo 13
SQL = """
    SELECT column_name 
    FROM information_schema.columns 
    WHERE table_name = 'ir_attachment' 
    ORDER BY ordinal_position
"""
cr_src.execute(SQL)
columns_src = [row['column_name'] for row in cr_src.fetchall()]
print(f"Colonnes disponibles dans Odoo 13: {', '.join(columns_src)}")

# Lister les colonnes disponibles dans Odoo 19
cr_dst.execute(SQL)
columns_dst = [row['column_name'] for row in cr_dst.fetchall()]
print(f"Colonnes disponibles dans Odoo 19: {', '.join(columns_dst)}")

# Colonnes communes
common_columns = set(columns_src) & set(columns_dst)
print(f"\nColonnes communes: {len(common_columns)}")

# Récupérer uniquement les attachments légitimes (pas les assets)
# Utiliser uniquement les colonnes qui existent dans les deux versions
select_fields = [
    'id', 'name', 'description', 'res_name', 'res_model', 'res_field', 'res_id',
    'company_id', 'type', 'url', 'public', 'access_token', 'store_fname', 'db_datas',
    'file_size', 'checksum', 'mimetype', 'index_content',
    'create_uid', 'create_date', 'write_uid', 'write_date'
]

# Filtrer les champs qui existent réellement
select_fields = [f for f in select_fields if f in columns_src]
print(f"Champs à migrer: {', '.join(select_fields)}")

SQL = f"""
    SELECT {', '.join(select_fields)}
    FROM ir_attachment
    WHERE 
        -- EXCLURE les assets web
        (name NOT LIKE '%.css' AND name NOT LIKE '%.js' AND name NOT LIKE '%.scss'
         AND name NOT LIKE '%.less' AND name NOT LIKE '%.sass')
        AND (url NOT LIKE '/web/assets/%' OR url IS NULL)
        AND (name NOT LIKE 'web.assets_%' OR name IS NULL)
        AND name NOT LIKE '%/static/%'
        -- EXCLURE les attachments système sans modèle
        AND NOT (res_model IS NULL AND type != 'binary')
        -- EXCLURE les models de configuration système
        AND res_model NOT IN ('res.partner', 'res.lang', 'res.company', 'res.country', 
                              'res.users', 'res.groups', 'ir.module.module')
        -- INCLURE uniquement les vrais documents
        AND (res_model IS NOT NULL OR store_fname IS NOT NULL)
    ORDER BY id
"""
cr_src.execute(SQL)
attachments = cr_src.fetchall()
print(f"\nTrouvé {len(attachments)} attachment(s) à migrer depuis Odoo 13")

# Compteurs
inserted = 0
skipped = 0
errors = 0
skipped_details = []

# Préparer les champs qui existent aussi dans la destination (en gardant l'ID)
insert_fields = [f for f in select_fields if f in columns_dst]
placeholders = ', '.join(['%s'] * len(insert_fields))

for att in attachments:
    try:
        # Vérifier si l'ID existe déjà
        cr_dst.execute("SELECT id, name, url, res_model, res_id FROM ir_attachment WHERE id = %s", [att['id']])
        existing = cr_dst.fetchone()
        if existing:
            skipped += 1
            skipped_details.append({
                'id': att['id'],
                'name_src': att.get('name'),
                'url_src': att.get('url'),
                'res_model_src': att.get('res_model'),
                'res_id_src': att.get('res_id'),
                'name_dst': existing['name'],
                'url_dst': existing['url'],
                'res_model_dst': existing['res_model'],
                'res_id_dst': existing['res_id'],
                'reason': 'ID existe'
            })
            continue
        
        # Vérifier si déjà existant (par checksum ou store_fname)
        check_sql = None
        check_params = []
        reason = None
        
        if att.get('checksum'):
            check_sql = "SELECT id, name, url, res_model, res_id FROM ir_attachment WHERE checksum = %s AND id != %s"
            check_params = [att['checksum'], att['id']]
            reason = 'Checksum existe'
        elif att.get('store_fname'):
            check_sql = "SELECT id, name, url, res_model, res_id FROM ir_attachment WHERE store_fname = %s AND id != %s"
            check_params = [att['store_fname'], att['id']]
            reason = 'store_fname existe'
        
        if check_sql:
            cr_dst.execute(check_sql, check_params)
            existing = cr_dst.fetchone()
            if existing:
                skipped += 1
                skipped_details.append({
                    'id': att['id'],
                    'name_src': att.get('name'),
                    'url_src': att.get('url'),
                    'res_model_src': att.get('res_model'),
                    'res_id_src': att.get('res_id'),
                    'name_dst': existing['name'],
                    'url_dst': existing['url'],
                    'res_model_dst': existing['res_model'],
                    'res_id_dst': existing['res_id'],
                    'reason': reason
                })
                continue
        
        # Préparer les valeurs (en incluant l'ID original)
        values = []
        for field in insert_fields:
            val = att.get(field)
            # Valeurs par défaut
            if field == 'type' and not val:
                val = 'binary'
            elif field == 'public' and val is None:
                val = False
            elif field in ['create_uid', 'write_uid'] and not val:
                val = 1
            values.append(val)
        
        # Insertion avec l'ID original
        SQL = f"""
            INSERT INTO ir_attachment ({', '.join(insert_fields)})
            VALUES ({placeholders})
        """
        cr_dst.execute(SQL, values)
        inserted += 1
        
        if inserted % 100 == 0:
            print(f"  {inserted} attachments insérés...")
            cnx_dst.commit()
            
    except Exception as e:
        errors += 1
        if errors < 10:  # Afficher les 10 premières erreurs
            print(f"  ERREUR sur {att.get('name', 'inconnu')}: {e}")

cnx_dst.commit()
print(f"\nRésumé migration ir_attachment:")
print(f"  - Insérés: {inserted}")
print(f"  - Ignorés (déjà existants): {skipped}")
print(f"  - Erreurs: {errors}")

# Afficher les détails des attachments ignorés
if skipped_details:
    print(f"\n=== DÉTAILS DES {len(skipped_details)} ATTACHMENTS IGNORÉS ===")
    print(f"{'ID':<5} {'Raison':<20} {'Nom SRC':<40} {'Model SRC':<20} {'Nom DST':<40} {'URL DST':<40} {'Model DST':<20}")
    print("-" * 190)
    for detail in skipped_details:  # Afficher TOUS sans limite
        name_src = (detail['name_src'] or '(vide)')[:39]
        model_src = detail['res_model_src'] or '(vide)'
        res_id_src = detail['res_id_src'] or ''
        model_res_src = f"{model_src}[{res_id_src}]"[:19]
        
        name_dst = (detail['name_dst'] or '(vide)')[:39]
        url_dst = (detail['url_dst'] or '(vide)')[:39]
        model_dst = detail['res_model_dst'] or '(vide)'
        res_id_dst = detail['res_id_dst'] or ''
        model_res_dst = f"{model_dst}[{res_id_dst}]"[:19]
        
        print(f"{detail['id']:<5} {detail['reason']:<20} {name_src:<40} {model_res_src:<20} {name_dst:<40} {url_dst:<40} {model_res_dst:<20}")

# Mettre à jour la séquence pour éviter les conflits d'ID futurs
print("\nMise à jour de la séquence ir_attachment_id_seq...")
SQL = """
    SELECT setval('ir_attachment_id_seq', 
                   (SELECT MAX(id) FROM ir_attachment));
"""
cr_dst.execute(SQL)
result = cr_dst.fetchone()
print(f"Séquence mise à jour: prochain ID = {result['setval'] + 1}")
cnx_dst.commit()
#******************************************************************************




