import { defineConfig } from 'vite';

export default defineConfig({
  root: '.',          // корневая папка проекта — если index.html в корне
  base: './',         // чтобы пути в сборке были относительные
  build: {
    outDir: 'dist'    // папка для сборки
  }
});
