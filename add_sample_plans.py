# add_sample_plans.py
from Shared import database


def main():
    # لیست سرورها برای اطمینان
    servers = database.get_servers()
    print("Servers:", servers)

    if not servers:
        print("❌ هیچ سروری ثبت نشده.")
        return

    # فعلاً فرض می‌کنیم فقط همین سرور هلند هست
    server = servers[0]
    server_id = server.get("id")
    print(f"Using server_id = {server_id}")

    # اگر همین الان پلنی ثبت شده، دیگه چیزی اضافه نکن
    existing = database.get_plans(server_id)
    print("Existing plans:", existing)
    if existing:
        print("✅ قبلاً پلن ثبت شده، کاری انجام ندادم.")
        return

    # اگر دسته‌بندی وجود ندارد، دو دسته می‌سازیم
    cats = database.get_plan_categories(server_id)
    if not cats:
        # اینجا priority رو هم می‌دیم (۱ و ۲)
        cat1_id = database.add_plan_category(server_id, "یک ماهه", 1)
        cat2_id = database.add_plan_category(server_id, "سه ماهه", 2)
        print("Created categories:", cat1_id, cat2_id)
        cats = database.get_plan_categories(server_id)

    # یک دیکشنری id → title برای دسته‌ها
    cat_by_title = {c["title"]: c["id"] for c in cats}

    cat_1m = cat_by_title.get("یک ماهه")
    cat_3m = cat_by_title.get("سه ماهه")

    print("Categories:", cats)

    # چند پلن نمونه
    # توجه: ترتیب آرگومان‌ها در add_plan دقیقا این است (طبق ساختار دیتابیس):
    # add_plan(server_id, category_id, price_toman, days, gb, title)

    if cat_1m:
        database.add_plan(server_id, cat_1m, 150_000, 30, 30, "30 گیگ / 30 روز")
        database.add_plan(server_id, cat_1m, 230_000, 30, 60, "60 گیگ / 30 روز")

    if cat_3m:
        database.add_plan(server_id, cat_3m, 450_000, 90, 150, "150 گیگ / 90 روز")

    # چاپ نتیجه نهایی
    final_plans = database.get_plans(server_id)
    print("Final plans:", final_plans)


if __name__ == "__main__":
    main()
