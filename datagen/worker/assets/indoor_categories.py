"""室内家居**类别白名单**——只允许室内房间里会自然出现的物体做 add/replace 主体。

为什么用白名单不用黑名单：LVIS 有 1200+ 类，黑名单挡不完（动物/车辆/户外/服饰/乐器/工业…）；
白名单虽会漏掉个别合理类，但**绝不会**产出"熊猫上椅子/驴进卧室/车上床"这种破坏数据质量的样本。
宁可少点多样性，也要场景协调。命中判断做归一化（小写、去 "_(fruit)" 后缀、连字符转下划线）。

维护：新增合理室内类往 INDOOR_ALLOWLIST 加即可。判断用 is_indoor(category)。
"""
from __future__ import annotations
import re

# 室内家居物体（按域组织，便于维护）。用 LVIS 风格基名（去括号后缀）。
INDOOR_ALLOWLIST = set()


def _add(*items):
    INDOOR_ALLOWLIST.update(items)


# —— 餐具 / 厨房小件 ——
_add("bowl", "plate", "cup", "mug", "glass", "wine_glass", "wineglass", "pitcher", "teapot",
     "kettle", "pot", "pan", "casserole", "saucepan", "frying_pan", "tray", "platter",
     "bottle", "water_bottle", "thermos", "flask", "jar", "can", "tin_can", "vase", "spice_rack",
     "cutting_board", "colander", "strainer", "ladle", "spatula", "fork", "spoon", "knife",
     "chopstick", "napkin", "napkin_holder", "salt_shaker", "pepper_mill", "sugar_bowl",
     "coffee_maker", "coffeepot", "toaster", "blender", "mixer", "microwave_oven", "cooker")

# —— 食物 / 农产品（放台面/桌面自然）——
_add("apple", "banana", "orange", "pear", "lemon", "lime", "peach", "grape", "strawberry",
     "pineapple", "watermelon", "melon", "tomato", "potato", "onion", "carrot", "turnip",
     "cucumber", "pumpkin", "corn", "mushroom", "bread", "bun", "bagel", "sandwich", "pizza",
     "cake", "cupcake", "cookie", "doughnut", "pie", "pastry", "egg", "cheese", "date",
     "truffle", "chocolate", "candy", "lollipop", "popcorn", "cereal", "fruit", "vegetable")

# —— 摆件 / 装饰 ——
_add("vase", "flower_arrangement", "flowerpot", "potted_plant", "houseplant", "plant",
     "picture_frame", "painting", "poster", "mirror", "clock", "alarm_clock", "candle",
     "candle_holder", "candlestick", "figurine", "statue_(sculpture)", "sculpture", "pottery",
     "bust", "ornament", "trophy", "globe", "hourglass", "wind_chime", "dreamcatcher",
     "bookend", "vase", "bowl", "basket", "wicker_basket", "birdcage", "lantern", "fishbowl",
     "aquarium", "terrarium")

# —— 书本 / 文具 / 桌面 ——
_add("book", "notebook", "magazine", "newspaper", "dictionary", "album", "diary",
     "pen", "pencil", "marker", "crayon", "eraser", "ruler", "scissors", "stapler",
     "tape_(sticky_cloth_or_paper)", "paperweight", "envelope", "folder", "clipboard",
     "calculator", "calendar", "business_card", "postcard", "map")

# —— 电子 / 电器 ——
_add("television_set", "television", "monitor_(computer_equipment)_computer_monitor", "monitor",
     "computer", "laptop_computer", "laptop", "keyboard_(computer_equipment)", "computer_keyboard",
     "mouse_(computer_equipment)", "computer_mouse", "tablet_computer", "cellular_telephone",
     "cellphone", "telephone", "remote_control", "speaker_(stereo_equipment)", "loudspeaker",
     "radio_receiver", "radio", "headset", "headphone", "earphone", "camera", "clock_radio",
     "printer", "router", "game_console", "controller_(control)", "joystick", "projector",
     "fan", "electric_fan", "heater", "humidifier", "air_conditioner", "iron_(for_clothing)",
     "hair_dryer", "vacuum_cleaner")

# —— 灯具 ——
_add("lamp", "table_lamp", "desk_lamp", "floor_lamp", "lampshade", "chandelier", "sconce",
     "light_bulb", "lightbulb", "nightlight", "flashlight")

# —— 织物 / 软装 ——
_add("pillow", "cushion", "throw_pillow", "blanket", "quilt", "comforter", "bedspread",
     "towel", "bath_towel", "washcloth", "rug", "carpet", "mat", "doormat", "placemat",
     "tablecloth", "curtain", "drape", "handkerchief")

# —— 卫浴 / 家居用品 ——
_add("toothbrush", "toothpaste", "soap", "soap_dispenser", "shampoo", "lotion", "hand_towel",
     "toilet_paper", "tissue_paper", "facial_tissue_holder", "comb", "hairbrush", "razor",
     "cosmetics", "perfume", "makeup", "hand_mirror", "cup", "toilet_brush", "plunger",
     "wastebasket", "trash_can", "bucket", "watering_can", "spray_bottle", "dustpan", "broom")

# —— 收纳 / 容器 ——
_add("box", "cardboard_box", "storage_box", "crate", "basket", "hamper", "bin", "canister",
     "tin", "chest", "trunk", "suitcase", "briefcase", "backpack", "handbag", "purse",
     "tote_bag", "shopping_bag", "gift_wrap", "present", "package")

# —— 玩具 / 休闲 ——
_add("teddy_bear", "doll", "toy", "ball", "balloon", "kite", "dice", "chess", "chessboard",
     "checkerboard", "playing_card", "puzzle", "rubiks_cube", "yo-yo",
     "building_block", "lego", "action_figure", "puppet", "board_game", "jigsaw")

# —— 家具（可作 replace 主体；add 会按 target_size 缩到小件）——
_add("stool", "footstool", "ottoman", "bench", "chair", "armchair", "recliner", "rocking_chair",
     "side_table", "end_table", "coffee_table", "nightstand", "desk", "table", "dining_table",
     "shelf", "bookshelf", "bookcase", "cabinet", "cupboard", "sideboard", "buffet_(food)",
     "dresser", "chest_of_drawers", "drawer", "wardrobe", "armoire", "filing_cabinet",
     "sofa", "couch", "loveseat", "sofa_bed", "bed", "futon", "crib", "bunk_bed", "headboard",
     "television_stand", "wine_rack", "spice_rack", "coatrack", "coat_hanger", "clothes_hanger",
     "hanger", "umbrella", "umbrella_stand", "cane", "walking_stick")

# —— 其它常见室内小物 ——
_add("key", "keychain", "wallet", "watch", "eyeglasses", "sunglasses", "hat", "cap", "scarf",
     "glove", "mitten", "slipper", "shoe", "sandal", "boot", "sock", "wristwatch",
     "cigarette", "tobacco_pipe", "ashtray", "lighter", "matchbox", "coin", "medal",
     "picture", "frame", "cd", "dvd", "cassette", "vinyl_record", "record_player")


def _norm(c) -> str:
    c = str(c).lower().strip()
    c = re.sub(r"_?\([^)]*\)", "", c)          # 去 "_(fruit)" / "(computer_equipment)" 后缀
    c = c.replace("-", "_")
    return re.sub(r"_+", "_", c).strip("_")


# 白名单条目本身也归一化，保证与 is_indoor 的查询同一形式（否则 "statue_(sculpture)" 存/查不一致）。
INDOOR_ALLOWLIST = {_norm(c) for c in INDOOR_ALLOWLIST}


# 壁挂 / 嵌入 / 管道 / 靠墙大件：原地旋转会穿墙脱墙、替换成紧凑物会浮在墙上或穿墙 → 这些
# 类别不适合当 rotate / replace 的主体（它们是"和墙融为一体"的，不能自由摆弄）。
WALL_INTEGRATED = {
    "cabinet", "cupboard", "wardrobe", "closet", "armoire", "mirror", "picture", "painting",
    "poster", "shelf", "shelves", "bookshelf", "bookcase", "headboard", "bed", "sink", "vanity",
    "toilet", "bathtub", "shower", "window", "door", "curtain", "drape", "blind", "blinds",
    "radiator", "fireplace", "stair", "stairs", "staircase", "counter", "countertop", "dishwasher",
    "washer", "dryer", "oven", "stove", "refrigerator", "fridge", "sconce", "chandelier",
}


def is_wall_integrated(category) -> bool:
    """该类别是否"和墙/结构融为一体"（不适合原地旋转或被替换）。"""
    if not category:
        return False
    n = _norm(category)
    return n in WALL_INTEGRATED or any(t in WALL_INTEGRATED for t in n.split("_"))


def is_indoor(category) -> bool:
    """该类别是否为室内家居可放置物（用于过滤 add/replace 的候选物体）。

    LVIS 类别是规范单名 → **精确匹配**（归一化后）。不做子词兜底：短词嵌在坏类名里会误判
    （如 "halter_top" 命中玩具 "top"）。宁可漏掉个别未收录的合理类（少点多样性），也不放错物进场景。
    """
    if not category:
        return False
    return _norm(category) in INDOOR_ALLOWLIST
