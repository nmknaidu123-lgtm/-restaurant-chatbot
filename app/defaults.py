"""
defaults.py - Starting data, used ONLY the first time the database is created.
After that, change everything from the Admin page (http://127.0.0.1:8000/admin).
"""

DEFAULT_SETTINGS = {
    "restaurant_name": "Spice Garden",
    "restaurant_info": (
        "Timings: 11:00 AM to 11:00 PM, all days\n"
        "Location: 12 MG Road, Hyderabad\n"
        "Parking: Free parking available\n"
        "Delivery: Available within 5 km\n"
        "Payment: Cash, UPI, Card\n"
        "Food: Veg and non-veg, Hyderabadi, North Indian, South Indian, Chinese\n"
        "Drinks: Tea, coffee, lassi, fresh juices, milkshakes, mocktails (no alcohol)"
    ),
}

# Demo staff accounts - change the passwords from the Admin page!
DEFAULT_STAFF = [
    ("admin", "admin123", "admin"),
    ("staff1", "staff123", "staff"),
]

DEFAULT_MENU = {
    "Veg Starters": {
        "Veg Manchurian": 160, "Gobi 65": 150, "Paneer Tikka": 220, "Paneer 65": 210,
        "Crispy Corn": 170, "Veg Spring Rolls": 150, "Mushroom Pepper Fry": 190,
    },
    "Non-Veg Starters": {
        "Chicken 65": 240, "Chilli Chicken": 250, "Chicken Lollipop": 260,
        "Tandoori Chicken (Half)": 280, "Apollo Fish": 300, "Prawns Fry": 340,
    },
    "Soups": {
        "Tomato Soup": 100, "Sweet Corn Soup": 110, "Hot and Sour Soup": 120,
        "Chicken Manchow Soup": 140,
    },
    "Biryani": {
        "Veg Biryani": 180, "Paneer Biryani": 220, "Egg Biryani": 200, "Chicken Biryani": 250,
        "Chicken 65 Biryani": 280, "Mutton Biryani": 350, "Prawns Biryani": 360,
    },
    "Veg Curries": {
        "Paneer Butter Masala": 220, "Kadai Paneer": 230, "Palak Paneer": 220,
        "Mixed Veg Curry": 180, "Dal Tadka": 150, "Dal Makhani": 170,
    },
    "Non-Veg Curries": {
        "Butter Chicken": 280, "Chicken Tikka Masala": 290, "Andhra Chicken Curry": 270,
        "Mutton Curry": 320, "Fish Curry": 300, "Egg Curry": 160,
    },
    "Breads": {
        "Roti": 25, "Butter Roti": 30, "Plain Naan": 35, "Butter Naan": 40,
        "Garlic Naan": 50, "Paratha": 45,
    },
    "Rice and Noodles": {
        "Steamed Rice": 90, "Jeera Rice": 130, "Curd Rice": 110, "Veg Fried Rice": 160,
        "Chicken Fried Rice": 200, "Veg Noodles": 160, "Chicken Noodles": 200,
    },
    "South Indian": {
        "Plain Dosa": 70, "Masala Dosa": 90, "Onion Dosa": 90, "Ghee Roast Dosa": 110,
        "Idli (2 pcs)": 60, "Vada (2 pcs)": 70, "Pongal": 80, "Upma": 70, "Pesarattu": 90,
    },
    "Desserts": {
        "Gulab Jamun": 70, "Double Ka Meetha": 90, "Qubani Ka Meetha": 120, "Rasmalai": 100,
        "Gajar Halwa": 100, "Vanilla Ice Cream": 80, "Chocolate Ice Cream": 90, "Kulfi": 80,
    },
    "Hot Drinks": {
        "Masala Chai": 30, "Ginger Tea": 30, "Green Tea": 50, "Filter Coffee": 40,
        "Cappuccino": 120, "Hot Chocolate": 130,
    },
    "Cold Drinks": {
        "Cold Coffee": 120, "Iced Tea": 100, "Fresh Lime Soda": 60, "Fresh Lime Water": 50,
        "Buttermilk": 40, "Soft Drink": 50, "Mineral Water": 30,
    },
    "Lassi": {
        "Sweet Lassi": 80, "Salt Lassi": 70, "Mango Lassi": 90, "Rose Lassi": 90,
    },
    "Fresh Juices": {
        "Orange Juice": 90, "Watermelon Juice": 80, "Pineapple Juice": 90,
        "Mosambi Juice": 80, "Pomegranate Juice": 120,
    },
    "Milkshakes": {
        "Chocolate Milkshake": 140, "Strawberry Milkshake": 140, "Mango Milkshake": 140,
        "Oreo Milkshake": 160, "Badam Milk": 100,
    },
    "Mocktails": {
        "Virgin Mojito": 150, "Blue Lagoon": 160, "Fruit Punch": 150, "Mint Cooler": 130,
    },
}


# Words that mark a dish as NON-VEG (used once to tag the menu; change any dish later in the Menu tab)
NON_VEG_WORDS = ("chicken", "mutton", "fish", "prawn", "egg", "keema", "lamb", "meat", "crab", "non-veg")

# Starting buffet packages for events (change them on the Events tab)
DEFAULT_PACKAGES = [
    {"name": "Veg Classic", "food_type": "Veg", "price_per_head": 350, "cost_per_head": 190,
     "description": "2 starters, 2 curries, biryani, naan and a sweet",
     "items": ["Veg Manchurian", "Gobi 65", "Paneer Butter Masala", "Dal Tadka", "Veg Biryani",
               "Butter Naan", "Gulab Jamun"]},
    {"name": "Veg Premium", "food_type": "Veg", "price_per_head": 500, "cost_per_head": 280,
     "description": "Paneer starters, 3 curries, biryani and rice, 2 desserts and a welcome drink",
     "items": ["Paneer Tikka", "Crispy Corn", "Kadai Paneer", "Dal Makhani", "Mixed Veg Curry", "Veg Biryani",
               "Jeera Rice", "Garlic Naan", "Double Ka Meetha", "Vanilla Ice Cream", "Sweet Lassi"]},
    {"name": "Non-Veg Classic", "food_type": "Non-veg", "price_per_head": 450, "cost_per_head": 260,
     "description": "Chicken and veg starters, butter chicken, dal, chicken biryani, naan and a sweet",
     "items": ["Chicken 65", "Veg Manchurian", "Butter Chicken", "Dal Tadka", "Chicken Biryani",
               "Butter Naan", "Gulab Jamun"]},
    {"name": "Non-Veg Premium", "food_type": "Non-veg", "price_per_head": 650, "cost_per_head": 380,
     "description": "3 starters, chicken and mutton curries, mutton biryani, 2 desserts and a drink",
     "items": ["Chicken Lollipop", "Apollo Fish", "Paneer Tikka", "Butter Chicken", "Mutton Curry",
               "Mutton Biryani", "Jeera Rice", "Garlic Naan", "Qubani Ka Meetha", "Vanilla Ice Cream",
               "Fresh Lime Soda"]},
]
