import os
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.cabinet.dependencies import get_current_admin_user
from app.database.database import get_db
from app.database.models import User, SystemSetting

router = APIRouter()

# Возможные переменные окружения для путей к фронтенду
# По умолчанию используем локальные пути разработчика
DEFAULT_INDEX_PATH = Path(os.getcwd()).parent / "bedolaga-cabinet" / "index.html"
DEFAULT_PUBLIC_DIR = Path(os.getcwd()).parent / "bedolaga-cabinet" / "public"

def get_index_path() -> Path:
    path_str = os.getenv("CABINET_INDEX_PATH")
    if path_str:
        return Path(path_str)
    return DEFAULT_INDEX_PATH

def get_public_dir() -> Path:
    path_str = os.getenv("CABINET_PUBLIC_DIR")
    if path_str:
        return Path(path_str)
    return DEFAULT_PUBLIC_DIR


async def set_system_setting(db: AsyncSession, key: str, value: str):
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = value
    else:
        setting = SystemSetting(key=key, value=value)
        db.add(setting)
    await db.commit()

async def get_system_setting(db: AsyncSession, key: str, default: str = "") -> str:
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalar_one_or_none()
    return setting.value if setting else default


def inject_seo_into_dist(index_path: Path, title: str, description: str, og_image_url: str):
    """
    Инжектит теги в уже собранный dist/index.html (если существует)
    для горячего обновления без необходимости пересборки фронтенда.
    """
    if not index_path.exists():
        return

    content = index_path.read_text(encoding="utf-8")

    # Обновляем <title>
    content = re.sub(r"<title>.*?</title>", f"<title>{title}</title>", content, flags=re.IGNORECASE)
    
    # Обновляем остальные теги
    tags = [
        ('name="description"', description),
        ('property="og:title"', title),
        ('property="og:description"', description),
        ('name="title"', title),
        ('property="twitter:title"', title),
        ('property="twitter:description"', description),
    ]

    for attr, value in tags:
        if f'<meta {attr}' in content:
            content = re.sub(
                fr'(<meta {attr} content=")([^"]*)(")',
                f'\\g<1>{value}\\g<3>',
                content,
                flags=re.IGNORECASE
            )
        else:
            content = content.replace("</head>", f'  <meta {attr} content="{value}">\n</head>')

    if og_image_url:
        for attr in ['property="og:image"', 'property="twitter:image"']:
            if f'<meta {attr}' in content:
                content = re.sub(
                    fr'(<meta {attr} content=")([^"]*)(")',
                    f'\\g<1>{og_image_url}\\g<3>',
                    content,
                    flags=re.IGNORECASE
                )
            else:
                content = content.replace("</head>", f'  <meta {attr} content="{og_image_url}">\n</head>')

    index_path.write_text(content, encoding="utf-8")


@router.post("/update", tags=["seo"], status_code=status.HTTP_200_OK)
async def update_seo_settings(
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
    title: str = Form(...),
    description: str = Form(...),
    og_image_url: str = Form(""),
    image_file: UploadFile | None = File(None),
):
    """
    Обновляет SEO настройки: сохраняет в Базу Данных (SystemSetting).
    Также патчит dist/index.html (если существует) для Production hot-update.
    """
    public_dir = get_public_dir()
    if not public_dir.exists():
        try:
            public_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass # Если не создалась - пропускаем, картинка просто не сохранится локально

    final_image_url = og_image_url

    if image_file and image_file.filename and public_dir.exists():
        ext = os.path.splitext(image_file.filename)[1] or ".jpg"
        target_filename = f"og-image{ext}"
        target_path = public_dir / target_filename
        with target_path.open("wb") as buffer:
            shutil.copyfileobj(image_file.file, buffer)
        
        if not final_image_url:
            final_image_url = f"/{target_filename}"
        elif final_image_url.endswith("/"):
            final_image_url = f"{final_image_url}{target_filename}"

    # Сохраняем в БД как SystemSetting (надежный источник правды)
    await set_system_setting(db, "SEO_TITLE", title)
    await set_system_setting(db, "SEO_DESCRIPTION", description)
    await set_system_setting(db, "SEO_OG_IMAGE_URL", final_image_url)

    # Патчим dist/index.html на случай если фронтенд собран и раздается через Nginx
    dist_dir = public_dir.parent / "dist"
    dist_index = dist_dir / "index.html"
    try:
        inject_seo_into_dist(dist_index, title, description, final_image_url)
    except Exception:
        pass

    return {"status": "success", "message": "SEO настройки сохранены в БД", "og_image": final_image_url}

class SeoSettingsResponse(BaseModel):
    title: str
    description: str
    og_image_url: str

@router.get("/current", tags=["seo"], response_model=SeoSettingsResponse)
async def get_current_seo(
    db: AsyncSession = Depends(get_db)  # Убрали проверку токена, чтобы Vite мог читать тэги в рантайме!
):
    """Get current SEO settings from Database or fallback to index.html"""
    title = await get_system_setting(db, "SEO_TITLE", "")
    description = await get_system_setting(db, "SEO_DESCRIPTION", "")
    og_image_url = await get_system_setting(db, "SEO_OG_IMAGE_URL", "")
    
    # Если в БД уже есть данные, возвращаем их
    if any([title, description, og_image_url]):
        return SeoSettingsResponse(title=title, description=description, og_image_url=og_image_url)

    # FALLBACK: Читаем из оригинального index.html, если БД пуста (для миграции локальных данных)
    index_path = get_index_path()
    if index_path.exists():
        try:
            content = index_path.read_text(encoding="utf-8")
            
            title_match = re.search(r'<title>(.*?)</title>', content, re.IGNORECASE)
            fallback_title = title_match.group(1) if title_match else ""
            if fallback_title and fallback_title != "Cabinet":
                title = fallback_title
                
            og_title_match = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']*)["\']', content, re.IGNORECASE)
            if og_title_match:
                title = og_title_match.group(1)
            
            desc_match = re.search(r'<meta\s+(?:name|property)=["\']description["\']\s+content=["\']([^"\']*)["\']', content, re.IGNORECASE)
            description = desc_match.group(1) if desc_match else ""
            
            img_match = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']*)["\']', content, re.IGNORECASE)
            og_image_url = img_match.group(1) if img_match else ""
        except Exception:
            pass
            
    return SeoSettingsResponse(title=title, description=description, og_image_url=og_image_url)

# [CODELOFT CUSTOM] Эндпоинты для Custom CSS
class CustomCssRequest(BaseModel):
    css_code: str

@router.get("/custom-css", tags=["seo"], response_model=CustomCssRequest)
async def get_custom_css(db: AsyncSession = Depends(get_db)):
    """Get custom CSS from Database. No auth required so frontend can inject it."""
    css_code = await get_system_setting(db, "CUSTOM_CSS", "")
    return CustomCssRequest(css_code=css_code)

@router.post("/custom-css", tags=["seo"], status_code=status.HTTP_200_OK)
async def update_custom_css(
    request: CustomCssRequest,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Save custom CSS to Database."""
    await set_system_setting(db, "CUSTOM_CSS", request.css_code)
    return {"status": "success", "message": "Custom CSS saved"}
