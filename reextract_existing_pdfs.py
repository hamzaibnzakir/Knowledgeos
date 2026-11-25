from pathlib import Path
import sqlite3
import subprocess
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else '/root/personal-ai-knowledge-os')
db_path = root / 'data' / '.brainbox' / 'knowledge.db'
vault = root / 'data' / 'vault'
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT id, filename, mime_type, path, extracted_text FROM attachments WHERE mime_type='application/pdf' OR lower(filename) LIKE '%.pdf'").fetchall()
repaired = []
for row in rows:
    source = Path(row['path'])
    if not source.exists():
        continue
    result = subprocess.run(['pdftotext', str(source), '-'], capture_output=True, text=True, timeout=60)
    text = result.stdout.strip()
    if not text:
        continue
    conn.execute('UPDATE attachments SET extracted_text=? WHERE id=?', (text, row['id']))
    note_rel = f"Attachments/{row['id']}-{Path(row['filename']).stem}.md"
    note_path = vault / note_rel
    if note_path.exists():
        note_path.write_text(f"# {row['filename']}\n\nSource: uploaded attachment\n\n{text}\n", encoding='utf-8')
    repaired.append({'filename': row['filename'], 'characters': len(text), 'note_path': note_rel})
conn.commit()
conn.close()
from knowledge_os_app import index_note
for item in repaired:
    note_path = vault / item['note_path']
    if note_path.exists():
        index_note(item['note_path'], note_path.read_text(encoding='utf-8'))
print({'pdfs_found': len(rows), 'repaired': repaired})
