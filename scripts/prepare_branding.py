import os
from PIL import Image

def analyze_and_trim():
    src_wordmark = "images/logo+wordmark.png"
    if not os.path.exists(src_wordmark):
        src_wordmark = "images/logo_wordmark.png"
    
    img = Image.open(src_wordmark)
    print(f"Original wordmark mode={img.mode}, size={img.size}")
    
    # Trim transparent borders
    bbox = img.getbbox()
    print(f"Non-transparent bounding box={bbox}")
    trimmed = img.crop(bbox)
    os.makedirs("app/static/images", exist_ok=True)
    trimmed.save("app/static/images/logo_wordmark.png")
    print(f"Saved trimmed wordmark: size={trimmed.size}")

    # Now let's crop the shield icon from the left side of trimmed
    # Let's inspect where the shield icon ends
    # The shield is approximately square / aspect ratio ~1:1 on the left
    w, h = trimmed.size
    print(f"Trimmed dimensions: width={w}, height={h}")
    # The shield icon occupies roughly the first h width
    # Let's sample the vertical transparent gap between shield and text if any
    shield_crop = trimmed.crop((0, 0, int(h * 1.05), h))
    shield_bbox = shield_crop.getbbox()
    if shield_bbox:
        shield_crop = shield_crop.crop(shield_bbox)
    
    # Make it square with transparent padding
    max_dim = max(shield_crop.width, shield_crop.height)
    square_icon = Image.new("RGBA", (max_dim, max_dim), (0, 0, 0, 0))
    offset_x = (max_dim - shield_crop.width) // 2
    offset_y = (max_dim - shield_crop.height) // 2
    square_icon.paste(shield_crop, (offset_x, offset_y))

    # Save favicon png and ico
    square_icon.resize((64, 64), Image.Resampling.LANCZOS).save("app/static/favicon.png")
    square_icon.resize((32, 32), Image.Resampling.LANCZOS).save("app/static/favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    print(f"Saved favicon.ico and favicon.png from cropped shield (dimension: {shield_crop.size})")

    # Also trim logo.png into app/static/images/logo.png
    if os.path.exists("images/logo.png"):
        logo = Image.open("images/logo.png")
        logo_bbox = logo.getbbox()
        if logo_bbox:
            logo = logo.crop(logo_bbox)
        logo.save("app/static/images/logo.png")
        print(f"Saved trimmed logo: size={logo.size}")

if __name__ == "__main__":
    analyze_and_trim()
