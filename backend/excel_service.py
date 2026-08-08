import os
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from config import config

def export_links_to_excel(links):
    if not os.path.exists(config.REPORTS_DIRECTORY):
        os.makedirs(config.REPORTS_DIRECTORY, exist_ok=True)
        
    filepath = os.path.join(config.REPORTS_DIRECTORY, "imported_links.xlsx")
    
    wb = Workbook()
    ws = wb.active
    ws.title = "WhatsApp Links"
    
    headers = ["Date", "Sender", "Message", "URL", "YouTube Video ID", "Status"]
    ws.append(headers)
    
    # Style header
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = Font(bold=True)
        
    for link in links:
        ws.append([
            link.message_date.strftime("%Y-%m-%d %H:%M:%S") if link.message_date else "",
            link.sender,
            link.original_message,
            link.extracted_url,
            link.youtube_video_id if link.youtube_video_id else "",
            link.status
        ])
        
    # Wrap text for Message column (C)
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=3, max_col=3):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True)
            
    # Set column widths
    ws.column_dimensions['A'].width = 20
    ws.column_dimensions['B'].width = 20
    ws.column_dimensions['C'].width = 50
    ws.column_dimensions['D'].width = 40
    ws.column_dimensions['E'].width = 20
    ws.column_dimensions['F'].width = 15
    
    # Freeze header
    ws.freeze_panes = "A2"
    
    wb.save(filepath)
    return filepath
