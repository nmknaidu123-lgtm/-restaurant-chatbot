"""
seed_staff.py - Adds 110 SAMPLE staff members to restaurant.db (for testing / demo).

Run once:   python seed_staff.py
Every sample account uses the password:  Staff@123
Running it again does not create duplicates.
"""

import random

from app.database import db, init_db, new_password_record

FIRST_NAMES = [
    "Ravi", "Suresh", "Ramesh", "Mahesh", "Naresh", "Srinivas", "Venkat", "Kiran", "Praveen", "Anil",
    "Sunil", "Vijay", "Ajay", "Rajesh", "Mohan", "Krishna", "Sai", "Harish", "Ganesh", "Prasad",
    "Arjun", "Rahul", "Karthik", "Vamsi", "Teja", "Chaitanya", "Nikhil", "Abhishek", "Manoj", "Deepak",
    "Lakshmi", "Priya", "Divya", "Swathi", "Kavya", "Anusha", "Sravani", "Bhavani", "Sowmya", "Ramya",
    "Padma", "Sirisha", "Haritha", "Keerthi", "Pooja", "Meena", "Sunitha", "Radha", "Geetha", "Madhavi",
]
LAST_NAMES = [
    "Reddy", "Rao", "Naidu", "Sharma", "Kumar", "Varma", "Chowdary", "Goud", "Yadav", "Patel",
    "Iyer", "Nair", "Raju", "Babu", "Shetty", "Gupta", "Verma", "Singh", "Das", "Pillai",
]

# (position, how many, role)
POSITIONS = [
    ("Manager", 4, "admin"),
    ("Assistant Manager", 6, "admin"),
    ("Head Chef", 3, "staff"),
    ("Chef", 14, "staff"),
    ("Tandoor Chef", 6, "staff"),
    ("Kitchen Helper", 12, "staff"),
    ("Waiter", 28, "staff"),
    ("Cashier", 8, "staff"),
    ("Host", 6, "staff"),
    ("Delivery Executive", 15, "staff"),
    ("Cleaner", 8, "staff"),
]
SHIFTS = ["Morning (7 AM - 3 PM)", "Evening (3 PM - 11 PM)", "Full day (11 AM - 11 PM)"]
PASSWORD = "Staff@123"


def main() -> None:
    init_db()
    random.seed(2026)                       # same sample data every time
    salt, pw_hash = new_password_record(PASSWORD)   # one hash, reused (fast)

    used_names = set()
    added = skipped = 0
    with db() as conn:
        for position, count, role in POSITIONS:
            for _ in range(count):
                while True:
                    full_name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
                    if full_name not in used_names:
                        used_names.add(full_name)
                        break
                username = full_name.lower().replace(" ", ".")      # e.g. ravi.reddy
                phone = "9" + "".join(random.choice("0123456789") for _ in range(9))
                shift = random.choice(SHIFTS)

                if conn.execute("SELECT 1 FROM staff WHERE username = ?", (username,)).fetchone():
                    skipped += 1
                    continue
                conn.execute(
                    "INSERT INTO staff (username, salt, password_hash, role, full_name, position, phone, shift) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (username, salt, pw_hash, role, full_name, position, phone, shift),
                )
                added += 1
        # Fill in details for the two starting accounts, if they are empty
        conn.execute("UPDATE staff SET full_name = 'Restaurant Admin', position = 'Owner', "
                     "shift = 'Full day (11 AM - 11 PM)' WHERE username = 'admin' AND position IS NULL")
        conn.execute("UPDATE staff SET full_name = 'Demo Staff', position = 'Waiter', "
                     "shift = 'Morning (7 AM - 3 PM)' WHERE username = 'staff1' AND position IS NULL")
        total = conn.execute("SELECT COUNT(*) FROM staff").fetchone()[0]

    print(f"Added {added} sample staff ({skipped} already existed).")
    print(f"Total staff in database: {total}")
    print(f"Sample login: username like 'ravi.reddy', password '{PASSWORD}'")


if __name__ == "__main__":
    main()
