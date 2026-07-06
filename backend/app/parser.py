import os
import io
from typing import List
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
import pdfplumber
import pytesseract
import fitz  # PyMuPDF
from PIL import Image
import docx


class MultiUserParser:
    @classmethod
    def parse_uploaded_stream(
        cls,
        file_bytes: bytes,
        file_name: str,
        user_id: str,
        session_id: str
    ) -> List[Document]:
        """
        Main multi-tenant entry point. Processes raw files from RAM bytes
        and routes them based on file extension.
        """
        ext = os.path.splitext(file_name)[1].lower()
        print(f"\n[Parser Gateway] User: {user_id} | Session: {session_id} | Processing: {file_name}")

        if ext == ".pdf":
            documents = cls._parse_pdf(file_bytes, file_name, user_id, session_id)
        elif ext in [".docx", ".doc"]:
            documents = cls._parse_docx(file_bytes, file_name, user_id, session_id)
        elif ext in [".txt", ".csv", ".md"]:
            documents = cls._parse_text_file(file_bytes, file_name, user_id, session_id)
        elif ext in [".png", ".jpg", ".jpeg", ".tiff"]:
            documents = cls._parse_image_ocr(file_bytes, file_name, user_id, session_id)
        else:
            print(f"Unsupported file format: {ext}")
            return []

        if not documents:
            print("No text could be extracted from the stream.")
            return []

        return cls._chunk_documents(documents, file_name, user_id, session_id)

    @classmethod
    def _parse_pdf(cls, file_bytes: bytes, file_name: str, user_id: str, session_id: str) -> List[Document]:
        parent_documents = []

        pypdf_stream = io.BytesIO(file_bytes)
        pdfplumber_stream = io.BytesIO(file_bytes)

        try:
            pypdf_reader = PdfReader(pypdf_stream)
        except Exception as e:
            print(f"Critical Error: Could not read metadata stream with pypdf. ({e})")
            return []

        with pdfplumber.open(pdfplumber_stream) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                try:
                    tables = []
                    raw_text = ""
                    used_fallback = False
                    needs_forced_rotation = False

                    has_images = False
                    if hasattr(page, 'images') and hasattr(page, 'rects'):
                        has_images = len(page.images) > 0 or len(page.rects) > 15

                    image_summary_context = "\n[Visual Content Note: Page contains embedded visual layers.]" if has_images else ""

                    try:
                        if page.width > page.height:
                            needs_forced_rotation = True
                        else:
                            chars = page.chars
                            if chars and len(chars) > 100:
                                sampled_chars = chars[::20]
                                vertical_chars = sum(1 for c in sampled_chars if c.get("orientation") in ["up", "down"] or c.get("upright") == 0)
                                if vertical_chars / len(sampled_chars) > 0.30:
                                    needs_forced_rotation = True
                    except Exception:
                        used_fallback = True

                    try:
                        if (page_num - 1) < len(pypdf_reader.pages):
                            pypdf_page = pypdf_reader.pages[page_num - 1]
                            native_rotation = pypdf_page.get("/Rotate", 0)
                        else:
                            native_rotation = 0
                    except Exception:
                        native_rotation = 0
                        used_fallback = True

                    if (native_rotation in [90, 270] or needs_forced_rotation) and not used_fallback:
                        rotation_angle = (360 - native_rotation) if native_rotation in [90, 270] else 90
                        try:
                            pypdf_page.rotate(rotation_angle)
                            raw_text = pypdf_page.extract_text() or ""
                            used_fallback = True
                        except Exception:
                            pass

                    if not used_fallback:
                        try:
                            tables = page.extract_tables()
                            raw_text = page.extract_text() or ""
                        except Exception:
                            try:
                                raw_text = pypdf_page.extract_text() or ""
                                used_fallback = True
                            except Exception:
                                pass

                    if used_fallback and not raw_text:
                        try:
                            raw_text = pypdf_page.extract_text() or ""
                        except Exception:
                            pass

                    # --- OCR FALLBACK LOGIC ---
                    # OCR triggers ONLY when extracted text is genuinely poor/missing.
                    def is_text_poor_quality(text: str) -> bool:
                        return len(text.strip()) < 50

                    if is_text_poor_quality(raw_text):
                        try:
                            pdf_doc = fitz.open(stream=file_bytes, filetype="pdf")
                            fitz_page = pdf_doc.load_page(page_num - 1)
                            # 150 DPI equivalent: zoom = dpi / 72
                            zoom = 150 / 72
                            pix = fitz_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
                            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

                            ocr_text = pytesseract.image_to_string(img, config='--psm 6')
                            if len(ocr_text.strip()) > len(raw_text.strip()):
                                raw_text = ocr_text
                                image_summary_context += "\n[System Note: Text extracted via OCR from scanned page layer.]"

                            img.close()
                            pdf_doc.close()
                        except Exception as e:
                            print(f"  OCR extraction fault on Page {page_num}: {e}")

                    cleaned_lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
                    sanitized_text = "\n".join(cleaned_lines)

                    table_markdown = ""
                    if tables and not used_fallback:
                        for table in tables:
                            cleaned_table = [[str(cell) if cell is not None else "" for cell in row] for row in table]
                            for row in cleaned_table:
                                if any(row):
                                    table_markdown += "| " + " | ".join(row) + " |\n"
                            table_markdown += "\n"

                    combined_content = f"ATTENTION LLM: FILE: '{file_name}' | PAGE: {page_num} {image_summary_context}\n" + sanitized_text
                    if table_markdown:
                        combined_content += "\n\n### Extracted Document Tables:\n" + table_markdown

                    parent_documents.append(
                        Document(
                            page_content=combined_content,
                            metadata={
                                "source": file_name,
                                "page": page_num,
                                "has_table": bool(table_markdown),
                                "has_images": has_images,
                                "user_id": user_id,
                                "session_id": session_id
                            }
                        )
                    )

                except Exception as page_e:
                    print(f"  Skipping severely corrupted layout on Page {page_num}: {page_e}")
                    continue

        return parent_documents

    @staticmethod
    def _parse_docx(file_bytes: bytes, file_name: str, user_id: str, session_id: str) -> List[Document]:
        doc_stream = io.BytesIO(file_bytes)
        doc = docx.Document(doc_stream)
        full_text = [para.text for para in doc.paragraphs if para.text.strip()]
        content = "\n".join(full_text)

        return [Document(
            page_content=f"ATTENTION LLM: FILE: '{file_name}'\n" + content,
            metadata={"source": file_name, "page": 1, "user_id": user_id, "session_id": session_id}
        )]

    @staticmethod
    def _parse_text_file(file_bytes: bytes, file_name: str, user_id: str, session_id: str) -> List[Document]:
        content = file_bytes.decode('utf-8', errors='ignore')
        return [Document(
            page_content=f"ATTENTION LLM: FILE: '{file_name}'\n" + content,
            metadata={"source": file_name, "page": 1, "user_id": user_id, "session_id": session_id}
        )]

    @staticmethod
    def _parse_image_ocr(file_bytes: bytes, file_name: str, user_id: str, session_id: str) -> List[Document]:
        try:
            image_stream = io.BytesIO(file_bytes)
            img = Image.open(image_stream)
            text = pytesseract.image_to_string(img)
        except Exception as e:
            print(f" OCR execution failed on raw image stream: {e}")
            text = ""

        return [Document(
            page_content=f"ATTENTION LLM: IMAGE FILE: '{file_name}'\n[System Note: Text extracted via OCR]\n" + text,
            metadata={"source": file_name, "page": 1, "is_image": True, "user_id": user_id, "session_id": session_id}
        )]

    @classmethod
    def _chunk_documents(cls, parent_documents: List[Document], original_file_name: str, user_id: str, session_id: str) -> List[Document]:
        final_chunks = []
        SAFE_PAGE_CHAR_LIMIT = 3000
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=2500, chunk_overlap=300, length_function=len)

        for doc in parent_documents:
            if len(doc.page_content) <= SAFE_PAGE_CHAR_LIMIT:
                final_chunks.append(doc)
                continue

            if "### Extracted Document Tables:" in doc.page_content:
                narrative, table_block = doc.page_content.split("### Extracted Document Tables:", 1)
                table_block = "### Extracted Document Tables:" + table_block
            else:
                narrative, table_block = doc.page_content, ""

            sub_docs = text_splitter.split_documents([Document(page_content=narrative, metadata=doc.metadata)])
            for sub in sub_docs:
                if table_block:
                    sub.page_content += "\n\n" + table_block
                final_chunks.append(sub)

        os.makedirs("./debug_logs", exist_ok=True)
        debug_output_path = f"./debug_logs/audit_{user_id}_{session_id}.txt"

        with open(debug_output_path, "a", encoding="utf-8") as txt_file:
            txt_file.write(f"=== CHUNK AUDIT LOG: {original_file_name} | {len(final_chunks)} SEGMENTS ===\n\n")
            for idx, chunk in enumerate(final_chunks, start=1):
                txt_file.write(f"--- CHUNK {idx} | Source: {chunk.metadata['source']} | Page: {chunk.metadata['page']} ---\n")
                txt_file.write(chunk.page_content)
                txt_file.write("\n\n" + "=" * 50 + "\n\n")

        return final_chunks
