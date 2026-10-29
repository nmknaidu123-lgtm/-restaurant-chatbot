"""
backup_now.py - Back up and export ALL restaurant data, without starting the server.

Run in the project folder:
    python backup_now.py

It makes:
  backups/recent/restaurant_<date>_manual.db   (full copy you can restore)
  backups/daily/restaurant_<date>.db           (today's copy, kept forever)
  backups/excel/restaurant_all_data_<date>.xlsx (everything in Excel)
"""
from app.backup import make_backup, export_excel, database_file

print(f"Database in use: {database_file()}")
db_copy = make_backup("manual")
excel = export_excel()
print(f"Backup copy : {db_copy}")
print(f"Excel file  : {excel}")
print("Done! Open the Excel file to see all your data.")
