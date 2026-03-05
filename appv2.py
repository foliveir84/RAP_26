import streamlit as st
import pandas as pd
import io
import os
from parser import process_pdf, process_excel

# --- Configuration & Helper Functions ---
st.set_page_config(
    page_title="Extrator de Faturas Farmácia v2",
    page_icon="💊",
    layout="wide",
    initial_sidebar_state="expanded"
)


def to_float_safe(val):
    """
    Converts a value to float, handling PT-PT format (comma decimals).
    Returns 0.0 if conversion fails.
    """
    if pd.isna(val) or val == "":
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)

    # Clean string: replace comma with dot, remove other non-numeric chars except dot/minus
    clean_val = str(val).replace(',', '.')
    try:
        return float(clean_val)
    except ValueError:
        return 0.0


# --- Sidebar ---
with st.sidebar:

    col_dev_1, col_dev_2 = st.columns([1, 4])
    with col_dev_1:
        ln_path = r'Logo_Pharmacoach.jpg'
        try:
            st.image(ln_path, width=300)
        except:
            pass

    st.markdown("---")

    with col_dev_2:
        st.markdown("[Pharmacoach](https://pharmacoach.up.railway.app/about)")
    st.image("https://cdn-icons-png.flaticon.com/512/883/883407.png", width=50)
    st.title("Configuração")

    st.markdown("### 1. Faturas (PDF ou XLSX)")
    uploaded_files = st.file_uploader(
        "Carregue os ficheiros aqui:",
        type=["pdf", "xlsx"],
        accept_multiple_files=True,
        help="Arraste e largue as faturas Cooprofar (PDF) ou Empifarma (XLSX)."
    )

    st.markdown("### 2. Base de Dados (PVP)")
    pvp_file = st.file_uploader(
        "Atualizar PVP Novos (Opcional):",
        type=["xlsx"],
        help="Se vazio, usa o 'pvp_novos.xlsx' do sistema."
    )

    st.markdown("---")
    st.caption(f"📅 Data: {pd.Timestamp.now().strftime('%d/%m/%Y')}")
    st.caption("v2.1 - Fornecedores (Cooprofar | Plural | Empifarma)")


# --- Main Interface ---
st.title("💊 Extrator de Faturas & Verificador de Preços")
st.markdown("""
Esta aplicação processa faturas **Cooprofar (PDF)** e **Empifarma (XLSX)**, extrai os produtos e compara os preços faturados com a tabela de **Novos PVPs**.
""")

# Status Area
status_container = st.container()

if uploaded_files:
    # Action Button
    col1, col2 = st.columns([1, 4])
    with col1:
        process_btn = st.button("🚀 Processar Faturas",
                                type="primary", use_container_width=True)

    if process_btn:
        with st.spinner('A ler ficheiros e a extrair dados...'):
            all_data = []

            # 1. Process Files
            progress_bar = status_container.progress(0)

            for idx, file in enumerate(uploaded_files):
                if file.name.lower().endswith('.pdf'):
                    # Process PDF (Cooprofar/Plural)
                    file_data = process_pdf(file, filename_override=file.name)
                elif file.name.lower().endswith('.xlsx'):
                    # Process Excel (Empifarma)
                    file_data = process_excel(
                        file, filename_override=file.name)
                else:
                    file_data = []

                all_data.extend(file_data)

                # Update progress
                progress_bar.progress((idx + 1) / len(uploaded_files))

            progress_bar.empty()

            if not all_data:
                st.error(
                    "❌ Não foram encontrados dados válidos nas faturas fornecidas.")
            else:
                # 2. Compile Data
                df_invoices = pd.DataFrame(all_data)

                # Normalize Columns & Types
                cols_order = [
                    'supplier', 'source_file', 'document_ref', 'page', 'prod_code', 'description',
                    'qty_ordered', 'qty_shipped', 'pvp', 'pvf',
                    'tax_percent', 'total_val', 'batch', 'tax_code_desc'
                ]

                # Ensure all columns exist
                for c in cols_order:
                    if c not in df_invoices.columns:
                        df_invoices[c] = ""

                # Rename supplier for UI
                df_invoices = df_invoices.rename(
                    columns={'supplier': 'Fornecedor'})
                cols_order[0] = 'Fornecedor'

                # Numeric Conversion
                numeric_cols = ['qty_ordered',
                                'qty_shipped', 'pvp', 'pvf', 'total_val']
                for col in numeric_cols:
                    df_invoices[col] = df_invoices[col].apply(to_float_safe)

                df_invoices['prod_code'] = df_invoices['prod_code'].astype(str)

                # 3. Load Comparison Data
                df_pvp_ref = None
                ref_source = "Nenhum"

                if pvp_file:
                    df_pvp_ref = pd.read_excel(pvp_file)
                    ref_source = "Upload do Utilizador"
                elif os.path.exists("pvp_novos.xlsx"):
                    df_pvp_ref = pd.read_excel("pvp_novos.xlsx")
                    ref_source = "Ficheiro de Sistema (pvp_novos.xlsx)"

                # 4. Compare Logic
                df_errors = pd.DataFrame()
                df_correct = pd.DataFrame()
                df_errors_export = pd.DataFrame()
                df_correct_export = pd.DataFrame()

                if df_pvp_ref is not None:
                    # Clean Ref Data
                    df_pvp_ref['NRegisto'] = df_pvp_ref['NRegisto'].astype(str)
                    df_pvp_ref['PVP Novo'] = df_pvp_ref['PVP Novo'].apply(
                        to_float_safe)

                    # Merge
                    merged = pd.merge(
                        df_invoices,
                        df_pvp_ref[['NRegisto', 'PVP Novo']],
                        left_on='prod_code',
                        right_on='NRegisto',
                        how='left'
                    )

                    # Identify Diffs (Threshold 0.01 for floating point safety)
                    merged['diff'] = merged['pvp'] - merged['PVP Novo']

                    # Filter: Match found AND diff exists
                    mask_diff = merged['PVP Novo'].notna() & (
                        merged['diff'].abs() > 0.01)
                    df_errors = merged[mask_diff].copy()

                    # Filter: Match found AND no diff (Correct prices)
                    mask_correct = merged['PVP Novo'].notna() & (
                        merged['diff'].abs() <= 0.01)
                    df_correct = merged[mask_correct].copy()

                    # Prepare Error Report
                    if not df_errors.empty:
                        df_errors_export = df_errors[[
                            'Fornecedor', 'source_file', 'document_ref', 'prod_code', 'description', 'qty_shipped', 'PVP Novo', 'pvp'
                        ]].rename(columns={
                            'source_file': 'Ficheiro',
                            'document_ref': 'Documento',
                            'prod_code': 'CNP',
                            'description': 'Descrição',
                            'qty_shipped': 'Unidades',
                            'pvp': 'PVP Faturado'
                        })

                    # Prepare Correct Report
                    if not df_correct.empty:
                        df_correct_export = df_correct[[
                            'Fornecedor', 'source_file', 'document_ref', 'prod_code', 'description', 'qty_shipped', 'PVP Novo', 'pvp'
                        ]].rename(columns={
                            'source_file': 'Ficheiro',
                            'document_ref': 'Documento',
                            'prod_code': 'CNP',
                            'description': 'Descrição',
                            'qty_shipped': 'Unidades',
                            'pvp': 'PVP Faturado'
                        })

                # --- Results Display ---

                # Summary Metrics
                m1, m2, m3 = st.columns(3)
                m1.metric("Ficheiros", len(uploaded_files))
                m2.metric("Linhas Extraídas", len(df_invoices))
                m3.metric("Erros de Preço", len(
                    df_errors), delta_color="inverse")

                # Tabs for different views
                tab1, tab2, tab3 = st.tabs(
                    ["⚠️ Erros de Preço", "✅ Preços Corretos", "📋 Dados Completos"])

                with tab1:
                    if df_errors.empty:
                        st.success(
                            "✅ Tudo limpo! Nenhuma discrepância de preço encontrada.")
                    else:
                        st.warning(
                            f"⚠️ Atenção: Foram encontradas {len(df_errors)} referências com preço incorreto.")
                        st.dataframe(
                            df_errors_export.style.format({
                                "Unidades": "{:.0f}",
                                "PVP Novo": "{:.2f} €",
                                "PVP Faturado": "{:.2f} €"
                            }),
                            use_container_width=True
                        )

                        # Download Errors
                        buffer_err = io.BytesIO()
                        with pd.ExcelWriter(buffer_err, engine='openpyxl') as writer:
                            df_errors_export.to_excel(writer, index=False)

                        st.download_button(
                            label="📥 Download Relatório de Erros (.xlsx)",
                            data=buffer_err,
                            file_name="Relatorio_Erros.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key="dl_err"
                        )

                with tab2:
                    if df_correct.empty:
                        st.info(
                            "ℹ️ Nenhum produto coincidente com a base de dados foi encontrado.")
                    else:
                        st.success(
                            f"💎 Encontradas {len(df_correct)} referências com preço correto.")
                        st.dataframe(
                            df_correct_export.style.format({
                                "Unidades": "{:.0f}",
                                "PVP Novo": "{:.2f} €",
                                "PVP Faturado": "{:.2f} €"
                            }),
                            use_container_width=True
                        )

                        # Download Correct
                        buffer_corr = io.BytesIO()
                        with pd.ExcelWriter(buffer_corr, engine='openpyxl') as writer:
                            df_correct_export.to_excel(writer, index=False)

                        st.download_button(
                            label="📥 Download Preços Corretos (.xlsx)",
                            data=buffer_corr,
                            file_name="Precos_Corretos.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key="dl_corr"
                        )

                with tab3:
                    st.dataframe(df_invoices[cols_order],
                                 use_container_width=True)

                    # Download Full
                    buffer_full = io.BytesIO()
                    with pd.ExcelWriter(buffer_full, engine='openpyxl') as writer:
                        df_invoices[cols_order].to_excel(writer, index=False)

                    st.download_button(
                        label="📥 Download Dados Completos (.xlsx)",
                        data=buffer_full,
                        file_name="Faturas_Compiladas.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="dl_full"
                    )

else:
    # Empty State
    st.info("👈 Comece por carregar os ficheiros (PDF ou XLSX) na barra lateral.")

    # Optional: Show if system reference file exists
    if os.path.exists("pvp_novos.xlsx"):
        st.success("✅ Base de dados 'pvp_novos.xlsx' detetada no sistema.")
    else:
        st.warning(
            "⚠️ Base de dados 'pvp_novos.xlsx' não encontrada. Terá de fazer upload manual.")
