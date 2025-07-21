import os
import time
import uuid
from datetime import datetime, timedelta
from flask import Flask, request, render_template, send_from_directory, jsonify
from PIL import Image
import fitz  # PyMuPDF for PDF processing
import os

# Initialize Flask app
app = Flask(__name__)

# Configuration for upload and processed folders
UPLOAD_FOLDER = 'uploads'
PROCESSED_FOLDER = 'processed'
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
ALLOWED_PDF_EXTENSIONS = {'pdf'}
# Files older than this many minutes will be deleted
FILE_LIFETIME_MINUTES = 20

# Create upload and processed directories if they don't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PROCESSED_FOLDER, exist_ok=True)


def allowed_file(filename, file_type='image'):
    """Checks if the uploaded file has an allowed extension based on type."""
    if file_type == 'image':
        return '.' in filename and \
            filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS
    elif file_type == 'pdf':
        return '.' in filename and \
            filename.rsplit('.', 1)[1].lower() in ALLOWED_PDF_EXTENSIONS
    return False


def cleanup_old_files():
    """Deletes files older than FILE_LIFETIME_MINUTES from specified folders."""
    now = datetime.now()
    for folder in [UPLOAD_FOLDER, PROCESSED_FOLDER]:
        for filename in os.listdir(folder):
            filepath = os.path.join(folder, filename)
            try:
                # Get file modification time
                file_mod_time = datetime.fromtimestamp(os.path.getmtime(filepath))
                if (now - file_mod_time) > timedelta(minutes=FILE_LIFETIME_MINUTES):
                    os.remove(filepath)
                    print(f"Cleaned up old file: {filepath}")
            except Exception as e:
                print(f"Error cleaning up file {filepath}: {e}")


@app.route('/')
def index():
    """Renders the main page of the application."""
    return render_template('index.html')


@app.route('/process', methods=['POST'])
def process_request():
    """Handles various processing requests: image resize, PDF to image, image to PDF."""
    cleanup_old_files()

    operation_type = request.form.get('operation_type')
    if not operation_type:
        return jsonify({'error': 'Operation type not specified'}), 400

    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    original_filename = file.filename
    file_extension = original_filename.rsplit('.', 1)[1].lower()
    unique_id = uuid.uuid4().hex
    upload_filepath = os.path.join(UPLOAD_FOLDER, f"{unique_id}_original.{file_extension}")

    try:
        if operation_type == 'resize_image':
            if not allowed_file(original_filename, 'image'):
                return jsonify({
                                   'error': 'Invalid file type for image resizing. Please upload an image (png, jpg, jpeg, gif, webp).'}), 400
            file.save(upload_filepath)
            return handle_image_resize(upload_filepath, original_filename, unique_id)

        elif operation_type == 'pdf_to_image':
            if not allowed_file(original_filename, 'pdf'):
                return jsonify(
                    {'error': 'Invalid file type for PDF to Image conversion. Please upload a PDF file (.pdf).'}), 400
            file.save(upload_filepath)
            return handle_pdf_to_image(upload_filepath, original_filename, unique_id)

        elif operation_type == 'image_to_pdf':
            if not allowed_file(original_filename, 'image'):
                return jsonify({
                                   'error': 'Invalid file type for Image to PDF conversion. Please upload an image (png, jpg, jpeg, gif, webp).'}), 400
            file.save(upload_filepath)
            return handle_image_to_pdf(upload_filepath, original_filename, unique_id)

        else:
            return jsonify({'error': 'Unknown operation type'}), 400

    except Exception as e:
        print(f"Error processing request: {e}")
        return jsonify({'error': f'An error occurred during processing: {str(e)}'}), 500


def handle_image_resize(image_path, original_filename, unique_id):
    """Handles image resizing and quality adjustment."""
    img = Image.open(image_path)

    width = request.form.get('width', type=int)
    height = request.form.get('height', type=int)
    percentage = request.form.get('percentage', type=int)
    quality_mode = request.form.get('quality_mode', 'lossless')
    jpeg_quality = request.form.get('jpeg_quality', type=int, default=85)

    original_width, original_height = img.size
    new_width, new_height = original_width, original_height

    # Define a default resize if no specific parameters are given
    DEFAULT_MAX_WIDTH = 800  # Common web-friendly width

    # Calculate new dimensions based on user input
    if percentage:
        if 0 < percentage <= 1000:
            new_width = int(original_width * (percentage / 100))
            new_height = int(original_height * (percentage / 100))
        else:
            return jsonify({'error': 'Percentage must be between 1 and 1000'}), 400
    elif width and height:
        if width > 0 and height > 0:
            new_width = width
            new_height = height
        else:
            return jsonify({'error': 'Width and Height must be positive integers'}), 400
    elif width:
        if width > 0:
            new_width = width
            new_height = int(original_height * (width / original_width))
        else:
            return jsonify({'error': 'Width must be a positive integer'}), 400
    elif height:
        if height > 0:
            new_height = height
            new_width = int(original_width * (height / original_height))
        else:
            return jsonify({'error': 'Height must be a positive integer'}), 400
    else:
        # If no specific resize parameters (width, height, percentage) are provided,
        # apply a default resize if the image is larger than the default max width.
        # This prevents upscaling of smaller images by default.
        if original_width > DEFAULT_MAX_WIDTH:
            new_width = DEFAULT_MAX_WIDTH
            new_height = int(original_height * (DEFAULT_MAX_WIDTH / original_width))
        # If the image is already smaller than or equal to DEFAULT_MAX_WIDTH,
        # it will retain its original dimensions (new_width, new_height remain original_width, original_height).

    resized_img = img.resize((new_width, new_height), Image.LANCZOS)

    output_filename = f"{unique_id}_resized.{'png' if quality_mode == 'lossless' else 'jpg'}"
    processed_filepath = os.path.join(PROCESSED_FOLDER, output_filename)

    if quality_mode == 'lossless':
        resized_img.save(processed_filepath, format='PNG')
    else:
        # Convert RGBA to RGB before saving as JPEG to avoid 'cannot write mode RGBA as JPEG' error
        if resized_img.mode == 'RGBA':
            # Create a new RGB image with a white background
            background = Image.new('RGB', resized_img.size, (255, 255, 255))
            background.paste(resized_img, mask=resized_img.split()[3])  # Use alpha channel as mask
            resized_img = background

        jpeg_quality = max(1, min(95, jpeg_quality))
        resized_img.save(processed_filepath, format='JPEG', quality=jpeg_quality)

    # Delete the original uploaded file after processing
    os.remove(image_path)

    return jsonify({
        'success': True,
        'operation': 'resize_image',
        'processed_files': [{
            'url': f'/processed/{output_filename}',
            'filename': output_filename,
            'original_size': f"{original_width}x{original_height}",
            'processed_size': f"{new_width}x{new_height}",
            'quality_mode': quality_mode,
            'jpeg_quality': jpeg_quality if quality_mode == 'lossy' else 'N/A'
        }],
        'original_filename': original_filename
    })


def handle_pdf_to_image(pdf_path, original_filename, unique_id):
    """Converts each page of a PDF to a separate image."""
    image_urls = []

    doc = fitz.open(pdf_path)
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        pix = page.get_pixmap()

        output_filename = f"{unique_id}_page_{page_num + 1}.png"
        processed_filepath = os.path.join(PROCESSED_FOLDER, output_filename)
        pix.save(processed_filepath)
        image_urls.append({
            'url': f'/processed/{output_filename}',
            'filename': output_filename,
            'page_number': page_num + 1
        })
    doc.close()

    # Delete the original uploaded file after processing
    os.remove(pdf_path)

    return jsonify({
        'success': True,
        'operation': 'pdf_to_image',
        'processed_files': image_urls,
        'original_filename': original_filename
    })


def handle_image_to_pdf(image_path, original_filename, unique_id):
    """Converts an image to a PDF document."""
    img = Image.open(image_path)

    # Convert image to RGB if it's not (important for saving to PDF)
    if img.mode == 'RGBA':
        img = img.convert('RGB')

    output_filename = f"{unique_id}_converted.pdf"
    processed_filepath = os.path.join(PROCESSED_FOLDER, output_filename)

    # Save image as PDF
    img.save(processed_filepath, format='PDF')

    # Delete the original uploaded file after processing
    os.remove(image_path)

    return jsonify({
        'success': True,
        'operation': 'image_to_pdf',
        'processed_files': [{
            'url': f'/processed/{output_filename}',
            'filename': output_filename
        }],
        'original_filename': original_filename
    })


@app.route('/processed/<filename>')
def serve_processed_file(filename):
    """Serves the processed files."""
    return send_from_directory(PROCESSED_FOLDER, filename)


@app.route('/uploads/<filename>')
def serve_uploaded_file(filename):
    """Serves the uploaded files (for preview if needed)."""
    return send_from_directory(UPLOAD_FOLDER, filename)


if __name__ == '__main__':
    # Initial cleanup on startup
    cleanup_old_files()

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

    app.run(debug=False)  # Set debug=False for production
