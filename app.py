import os
import uuid
import zipfile
import tempfile
from flask import Flask, request, jsonify, render_template, send_from_directory, url_for
from PIL import Image, ImageOps  # Import ImageOps for EXIF handling
import fitz  # PyMuPDF for PDF processing
import math
import shutil
import time  # Import time module for file age checking

app = Flask(__name__)

# Configuration for upload and processed folders
UPLOAD_FOLDER = 'uploads'
PROCESSED_FOLDER = 'processed'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['PROCESSED_FOLDER'] = PROCESSED_FOLDER

# Ensure upload and processed directories exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PROCESSED_FOLDER, exist_ok=True)

# Allowed extensions
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
ALLOWED_PDF_EXTENSIONS = {'pdf'}


def allowed_file(filename, allowed_extensions):
    """Checks if the file extension is allowed."""
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in allowed_extensions


def get_file_size_display(file_path):
    """Returns file size in a human-readable format."""
    size_bytes = os.path.getsize(file_path)
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"


# Function to clean up old files
def cleanup_old_files(age_hours=20 / 60):  # Changed to 20 minutes
    """Removes files older than age_hours from upload and processed folders."""
    now = time.time()
    cutoff_time = now - (age_hours * 3600)  # 3600 seconds in an hour

    for folder in [app.config['UPLOAD_FOLDER'], app.config['PROCESSED_FOLDER']]:
        for filename in os.listdir(folder):
            file_path = os.path.join(folder, filename)
            try:
                if os.path.isfile(file_path):
                    file_mod_time = os.path.getmtime(file_path)
                    if file_mod_time < cutoff_time:
                        os.remove(file_path)
                        app.logger.info(f"Cleaned up old file: {filename}")
            except Exception as e:
                app.logger.error(f"Error cleaning up file {filename}: {e}")


@app.route('/')
def index():
    """Renders the main page."""
    return render_template('index.html')


@app.route('/about')
def about():
    """Renders the about page."""
    return render_template('about.html')


# Route for the Contact page
@app.route('/contact')
def contact():
    """Renders the contact page."""
    return render_template('contact.html')


@app.route('/process', methods=['POST'])
def process_file():
    """Handles file upload and processing."""
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file part'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No selected file'}), 400

    operation_type = request.form.get('operation_type')
    if not operation_type:
        return jsonify({'success': False, 'error': 'No operation type selected'}), 400

    original_filename = file.filename
    file_extension = original_filename.rsplit('.', 1)[1].lower()
    unique_filename = str(uuid.uuid4()) + '.' + file_extension
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
    file.save(file_path)

    processed_files_info = []
    download_all_url = None  # Initialize for PDF zipping
    # temp_image_paths is no longer needed for cleanup in finally, as PROCESSED_FOLDER is cleaned by cleanup_old_files
    # temp_image_paths = [] # Removed as it's no longer used for immediate cleanup

    try:
        if operation_type == 'resize_image':
            if not allowed_file(original_filename, ALLOWED_IMAGE_EXTENSIONS):
                return jsonify({'success': False, 'error': 'Unsupported image file type.'}), 400

            img = Image.open(file_path)
            # Apply EXIF orientation to the image
            img = ImageOps.exif_transpose(img)

            original_size_bytes = os.path.getsize(file_path)  # Get raw bytes for comparison
            original_size_display = get_file_size_display(file_path)

            width = request.form.get('width', type=int)
            height = request.form.get('height', type=int)
            percentage = request.form.get('percentage', type=int)
            quality_mode = request.form.get('quality_mode', 'lossless')
            jpeg_quality = request.form.get('jpeg_quality', type=int, default=85)

            # Automatic compression to KB if original is MB and no explicit resize options are chosen
            # Check if original size is 1MB or more AND no specific dimensions/percentage were provided
            if original_size_bytes >= 1024 * 1024 and not (width or height or percentage):
                quality_mode = 'lossy'  # Force to lossy (JPEG) for compression
                # jpeg_quality will use its default (85) or whatever was sent from the form, which is fine.
                app.logger.info("Automatically applying JPEG compression for large file without explicit resize.")

            # Calculate new dimensions
            new_width, new_height = img.size
            if percentage:
                scale_factor = percentage / 100.0
                new_width = int(img.size[0] * scale_factor)
                new_height = int(img.size[1] * scale_factor)
            elif width and height:
                new_width = width
                new_height = height
            elif width:
                new_height = int(img.size[1] * (width / img.size[0]))
                new_width = width
            elif height:
                new_width = int(img.size[0] * (height / img.size[1]))
                new_height = height
            else:  # Default behavior if no dimensions/percentage are provided at all
                # This block will now be reached if the auto-compression above didn't change dimensions
                # or if the image was already small. Max width 1200 is a reasonable default.
                max_width = 1200
                if img.size[0] > max_width:
                    new_width = max_width
                    new_height = int(img.size[1] * (max_width / img.size[0]))

            # Resize the image using LANCZOS for high quality
            resized_img = img.resize((new_width, new_height), Image.LANCZOS)

            # Determine output format and save
            output_extension = 'png' if quality_mode == 'lossless' else 'jpeg'
            processed_filename = f"{os.path.splitext(original_filename)[0]}_processed_{uuid.uuid4().hex[:8]}.{output_extension}"
            processed_file_path = os.path.join(app.config['PROCESSED_FOLDER'], processed_filename)

            if quality_mode == 'lossy':
                # Handle transparency for JPEG by compositing on white background
                if resized_img.mode in ('RGBA', 'LA') or (
                        resized_img.mode == 'P' and 'transparency' in resized_img.info):
                    alpha = resized_img.convert('RGBA').split()[-1]
                    bg = Image.new("RGB", resized_img.size, (255, 255, 255))
                    bg.paste(resized_img, mask=alpha)
                    resized_img = bg
                resized_img.save(processed_file_path, quality=jpeg_quality, optimize=True)
            else:  # Lossless (PNG)
                resized_img.save(processed_file_path)

            processed_size_display = get_file_size_display(processed_file_path)
            processed_files_info.append({
                'filename': processed_filename,
                'url': f'/processed/{processed_filename}',
                'original_size': original_size_display,
                'processed_size': processed_size_display,
                'quality_mode': quality_mode,
                'jpeg_quality': jpeg_quality if quality_mode == 'lossy' else None
            })

        elif operation_type == 'pdf_to_image':
            if not allowed_file(original_filename, ALLOWED_PDF_EXTENSIONS):
                return jsonify({'success': False, 'error': 'Unsupported PDF file type.'}), 400

            doc = fitz.open(file_path)
            # List to store filenames of individual images for zipping
            # These files will remain in PROCESSED_FOLDER until cleanup_old_files removes them
            individual_image_filenames_for_zip = []

            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                pix = page.get_pixmap()
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

                page_image_filename = f"{os.path.splitext(original_filename)[0]}_page_{page_num + 1}_{uuid.uuid4().hex[:8]}.png"
                page_image_path = os.path.join(app.config['PROCESSED_FOLDER'], page_image_filename)
                img.save(page_image_path)
                individual_image_filenames_for_zip.append(page_image_filename) # Add to list for zipping

                processed_files_info.append({
                    'filename': page_image_filename,
                    'url': f'/processed/{page_image_filename}',  # Correct URL for serving
                    'page_number': page_num + 1
                })
            doc.close()

            # Create a zip file of all processed images
            zip_filename = f"{os.path.splitext(original_filename)[0]}_images_{uuid.uuid4().hex[:8]}.zip"
            zip_file_path = os.path.join(app.config['PROCESSED_FOLDER'], zip_filename)

            with zipfile.ZipFile(zip_file_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for img_name in individual_image_filenames_for_zip:
                    # Write the file from its actual location in PROCESSED_FOLDER to the zip
                    zipf.write(os.path.join(app.config['PROCESSED_FOLDER'], img_name), img_name)

            download_all_url = f'/processed/{zip_filename}'  # URL for the zip file

        elif operation_type == 'image_to_pdf':
            if not allowed_file(original_filename, ALLOWED_IMAGE_EXTENSIONS):
                return jsonify({'success': False, 'error': 'Unsupported image file type.'}), 400

            img = Image.open(file_path)
            # Apply EXIF orientation to the image before converting to PDF
            img = ImageOps.exif_transpose(img)

            # Convert to RGB if not already to avoid issues with some image modes in PDF conversion
            if img.mode in ('RGBA', 'LA', 'P'):
                alpha = img.convert('RGBA').split()[-1]
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=alpha)
                img = bg
            elif img.mode != 'RGB':
                img = img.convert('RGB')

            pdf_filename = f"{os.path.splitext(original_filename)[0]}_converted_{uuid.uuid4().hex[:8]}.pdf"
            pdf_file_path = os.path.join(app.config['PROCESSED_FOLDER'], pdf_filename)

            img.save(pdf_file_path, "PDF", resolution=100.0)

            processed_files_info.append({
                'filename': pdf_filename,
                'url': f'/processed/{pdf_filename}'
            })

        else:
            return jsonify({'success': False, 'error': 'Invalid operation type.'}), 400

        return jsonify({
            'success': True,
            'original_filename': original_filename,
            'operation': operation_type,
            'processed_files': processed_files_info,
            'download_all_url': download_all_url  # Include the zip URL
        }), 200

    except Exception as e:
        app.logger.error(f"Error processing file: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'An error occurred during processing: {str(e)}'}), 500
    finally:
        # Clean up the original uploaded file only
        if os.path.exists(file_path):
            os.remove(file_path)
        # Removed the problematic cleanup of individual PDF images here.
        # These are now handled by the periodic cleanup_old_files function.


@app.route('/processed/<filename>')
def serve_processed_file(filename):
    """Serves processed files from the PROCESSED_FOLDER."""
    return send_from_directory(app.config['PROCESSED_FOLDER'], filename)


if __name__ == '__main__':
    # Initial cleanup on startup
    cleanup_old_files()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
