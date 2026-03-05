import pdfplumber
import pandas as pd
import re
import os

def process_excel(file_path_or_obj, filename_override=None):
    """
    Processes Excel files, specifically for Empifarma.
    """
    if filename_override:
        filename = filename_override
    elif isinstance(file_path_or_obj, str):
        filename = os.path.basename(file_path_or_obj)
    else:
        filename = "unknown_file.xlsx"

    extracted_lines = []
    try:
        df = pd.read_excel(file_path_or_obj)
        
        # Check for Empifarma columns
        empifarma_cols = ['documento', 'codigo', 'designacao', 'quantidadePedida', 'pvp']
        if all(col in df.columns for col in empifarma_cols):
            supplier = "Empifarma"
            # Filter quantidadePedida > 0 (as instructed, this contains effectively shipped quantity)
            df_filtered = df[df['quantidadePedida'] > 0].copy()
            
            for _, row in df_filtered.iterrows():
                extracted_lines.append({
                    'supplier': supplier,
                    'prod_code': str(row['codigo']),
                    'description': str(row['designacao']),
                    'qty_ordered': "", # We use quantidadePedida as qty_shipped
                    'qty_shipped': str(row['quantidadePedida']),
                    'pvp': str(row['pvp']),
                    'source_file': filename,
                    'document_ref': str(row['documento']),
                    'page': 1 
                })
        else:
            print(f"Excel file {filename} does not match Empifarma format.")
            
    except Exception as e:
        print(f"Error processing Excel {filename}: {e}")
        
    return extracted_lines

def process_pdf(pdf_path_or_obj, filename_override=None):
    extracted_lines = []
    
    # Determine filename: use override if provided (for Streamlit), else extract from path
    if filename_override:
        filename = filename_override
    elif isinstance(pdf_path_or_obj, str):
        filename = os.path.basename(pdf_path_or_obj)
    else:
        filename = "unknown_file.pdf"
    
    try:
        with pdfplumber.open(pdf_path_or_obj) as pdf:
            # 1. Detect Supplier
            supplier = "Desconhecido"
            for p in range(min(3, len(pdf.pages))):
                text_sample = pdf.pages[p].extract_text(layout=True) or ""
                if "COOPROFAR" in text_sample.upper():
                    supplier = "Cooprofar"
                    break
                elif "PLURAL" in text_sample.upper():
                    supplier = "Plural"
                    break

            for i, page in enumerate(pdf.pages):
                text = page.extract_text(layout=True)
                if not text:
                    continue
                
                # Skip Duplicates
                if "ODACILPUD" in text or "DUPLICADO" in text:
                    continue
                
                lines = text.split('\n')
                
                if supplier == "Cooprofar":
                    # --- Cooprofar Logic ---
                    main_block_rows = []
                    desc_block_rows = []
                    
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        
                        if ("Legenda" in line or "P O V P A V" in line or "TOTAL" in line or "SUBTOTAL" in line 
                            or "TRANSPORTAR" in line or "TRANSPORTE" in line or "Origem junta" in line 
                            or "[" in line or "]" in line or "incumprimento" in line):
                            continue

                        if re.match(r'^\d{7}\s+', line):
                            tokens = line.split()
                            if len(tokens) >= 4 and tokens[1].isdigit() and tokens[2].isdigit():
                                row_data = {
                                    'prod_code': tokens[0],
                                    'qty_ordered': tokens[1],
                                    'qty_shipped': tokens[2],
                                    'pvp': tokens[3],
                                    'tax_percent': tokens[4] if len(tokens) > 4 else '',
                                    'pvf': tokens[5] if len(tokens) > 5 else '',
                                    'total_val': tokens[6] if len(tokens) > 6 else ''
                                }
                                main_block_rows.append(row_data)
                                
                        elif re.match(r'^[A-Z][0-9]?\s+', line):
                            tokens = line.split()
                            if len(tokens) >= 3:
                                tax_code = tokens[0]
                                batch = tokens[-1]
                                price_val = tokens[-2]
                                description_tokens = tokens[1:-2]
                                description = " ".join(description_tokens)
                                
                                desc_data = {
                                    'tax_code_desc': tax_code,
                                    'description': description,
                                    'val_desc': price_val,
                                    'batch': batch
                                }
                                desc_block_rows.append(desc_data)

                    limit = min(len(main_block_rows), len(desc_block_rows))
                    for idx in range(limit):
                        merged = main_block_rows[idx].copy()
                        merged.update(desc_block_rows[idx])
                        merged['supplier'] = supplier
                        merged['source_file'] = filename
                        merged['page'] = i + 1
                        extracted_lines.append(merged)

                elif supplier == "Plural":
                    # --- Plural Logic ---
                    for line in lines:
                        line = line.strip()
                        # Plural lines contain a 7-digit code
                        match_cnp = re.search(r'\b(\d{7})\b', line)
                        if match_cnp:
                            cnp = match_cnp.group(1)
                            tokens = line.split()
                            
                            try:
                                cnp_idx = tokens.index(cnp)
                            except ValueError:
                                # CNP found but not as a standalone token (e.g. part of another string)
                                continue

                            if len(tokens) > cnp_idx + 3:
                                # Find indicators (%, or price with tax letter)
                                idx_indicator = -1
                                for k in range(cnp_idx + 1, len(tokens)):
                                    if '%' in tokens[k] or re.search(r'\d+,\d+[ATGN]\b', tokens[k]):
                                        idx_indicator = k
                                        break
                                
                                if idx_indicator != -1:
                                    sub_tokens = tokens[cnp_idx+1 : idx_indicator+1]
                                    
                                    # Quantities are consecutive digits before prices
                                    # Let's find all digit-only tokens
                                    digit_tokens = [(k, t) for k, t in enumerate(tokens) if t.isdigit() and k > cnp_idx]
                                    
                                    qty_shipped = None
                                    # Look for a pair of digits or the last digits before prices
                                    if len(digit_tokens) >= 2:
                                        # Usually QEnc QForn are together
                                        for d in range(len(digit_tokens)-1):
                                            if digit_tokens[d+1][0] == digit_tokens[d][0] + 1:
                                                qty_shipped = digit_tokens[d+1][1]
                                                break
                                        if not qty_shipped:
                                            qty_shipped = digit_tokens[-1][1]
                                    elif len(digit_tokens) == 1:
                                        qty_shipped = digit_tokens[0][1]
                                    
                                    # Prices have commas and are NOT like "0,5MG/G"
                                    # We look for price pattern: digits + comma + digits (usually 2)
                                    prices = [t for t in sub_tokens if re.match(r'^\d+,\d{2}$', t)]
                                    
                                    pvp = None
                                    if prices:
                                        pvp = prices[0] # First one is PVP
                                    elif re.search(r'(\d+,\d{2})[ATGN]\b', tokens[idx_indicator]):
                                        # If no standalone price found, maybe it's the indicator itself
                                        pvp = re.search(r'(\d+,\d{2})[ATGN]\b', tokens[idx_indicator]).group(1)
                                
                                    if pvp and qty_shipped:
                                        extracted_lines.append({
                                            'supplier': supplier,
                                            'prod_code': cnp,
                                            'description': " ".join(tokens[cnp_idx+1 : cnp_idx+5]), 
                                            'qty_ordered': '', 
                                            'qty_shipped': qty_shipped,
                                            'pvp': pvp,
                                            'source_file': filename,
                                            'page': i + 1
                                        })

    except Exception as e:
        print(f"Error processing {filename}: {e}")
        
    return extracted_lines
