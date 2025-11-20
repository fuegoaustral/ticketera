#!/usr/bin/env python3
"""
Script para procesar CSV de asignación de bonos y convertirlo al formato
necesario para importar DirectTicketTemplate.

Formato de entrada: tipo - subtipo,Nombre,Apellido,Email,amount
Formato de salida: origin,name,amount
"""

import csv
import sys
import os

def normalize_origin(origin_str):
    """Normaliza el origen: VOL -> VOLUNTARIOS, ORG -> ORGANIZACION, mantiene ARTE y CAMP"""
    origin_upper = origin_str.strip().upper()
    if origin_upper == 'VOL':
        return 'VOLUNTARIOS'
    elif origin_upper == 'ARTE':
        return 'ARTE'
    elif origin_upper == 'CAMP':
        return 'CAMP'
    elif origin_upper == 'ORG':
        return 'ORGANIZACION'
    else:
        return origin_upper

def process_csv(input_file, output_file):
    """Procesa el CSV y genera el formato para DirectTicketTemplate"""
    
    with open(input_file, 'r', encoding='utf-8') as infile, \
         open(output_file, 'w', encoding='utf-8', newline='') as outfile:
        
        reader = csv.reader(infile)
        writer = csv.writer(outfile)
        
        # Escribir header
        writer.writerow(['origin', 'name', 'amount', 'email'])
        
        for row in reader:
            if not row or len(row) < 5:
                continue  # Necesitamos al menos: tipo, nombre, apellido, email, amount
            
            # Primera columna: tipo - subtipo
            tipo_col = row[0].strip()
            
            # Split por " - " y trimear
            if ' - ' in tipo_col:
                parts = [p.strip() for p in tipo_col.split(' - ', 1)]
                origin_part = parts[0]
                subtipo = parts[1] if len(parts) > 1 else ''
            else:
                # Si no hay " - ", asumimos que todo es el subtipo
                origin_part = tipo_col
                subtipo = ''
            
            # Normalizar origin
            origin = normalize_origin(origin_part)
            
            # Construir name: subtipo + " " + nombre + " " + apellido
            # Columnas: tipo, nombre, apellido, [booleano opcional], email, amount
            nombre = row[1].strip() if len(row) > 1 else ''
            apellido = row[2].strip() if len(row) > 2 else ''
            
            # El email puede estar en la columna 3 o 4 dependiendo de si hay un booleano
            # Si la columna 3 parece un email (contiene @), usarla, sino usar la 4
            email = ''
            if len(row) > 3:
                if '@' in row[3]:
                    email = row[3].strip()
                elif len(row) > 4:
                    email = row[4].strip()
            
            # Construir name (sin email, el email va en su propia columna)
            name_parts = []
            if subtipo:
                name_parts.append(subtipo)
            if nombre:
                name_parts.append(nombre)
            if apellido:
                name_parts.append(apellido)
            
            name = ' '.join(name_parts)
            
            # Amount es la última columna
            amount = row[-1].strip() if row else '0'
            
            # Escribir fila procesada: origin, name, amount, email
            writer.writerow([origin, name, amount, email])
    
    print(f"✅ CSV procesado exitosamente!")
    print(f"📥 Archivo de entrada: {input_file}")
    print(f"📤 Archivo de salida: {output_file}")

if __name__ == '__main__':
    input_file = '/Users/estoussel/Downloads/Asignacion de bonos 2026 - General_FINAL.csv'
    output_file = '/Users/estoussel/Downloads/Asignacion de bonos 2026 - General_FINAL_processed.csv'
    
    if len(sys.argv) > 1:
        input_file = sys.argv[1]
    if len(sys.argv) > 2:
        output_file = sys.argv[2]
    
    if not os.path.exists(input_file):
        print(f"❌ Error: No se encuentra el archivo {input_file}")
        sys.exit(1)
    
    process_csv(input_file, output_file)

