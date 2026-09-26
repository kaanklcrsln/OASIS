import type { PhotoInfo } from './types';

/** Reads dimensions and builds a small thumbnail that fits in localStorage. */
export function readImage(file: File, thumbSize = 160): Promise<PhotoInfo> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, thumbSize / Math.max(img.naturalWidth, img.naturalHeight));
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
      canvas.getContext('2d')?.drawImage(img, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      resolve({
        width: img.naturalWidth,
        height: img.naturalHeight,
        thumbnail: canvas.toDataURL('image/jpeg', 0.7),
      });
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('Görsel okunamadı'));
    };
    img.src = url;
  });
}
