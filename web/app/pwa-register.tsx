'use client';
import { useEffect } from 'react';

export default function PWARegister() {
  useEffect(() => {
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/sw.js').catch((error) => {
        console.error('FINN service worker registration failed:', error);
      });
    }
  }, []);
  return null;
}
