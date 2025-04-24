
import express from 'express';
import cors from 'cors';

import { createRouter } from './router';


export type StartServerOptions = {
    host?: string;
    port?: number;
    basePath?: string;
}

export function startServer(options?: StartServerOptions) {
  const router = createRouter();

  const host = options?.host ?? 'localhost';
  const port = options?.port ?? 3000;
  const basePath = options?.basePath ?? '/';

  const app = express();
  app.use(basePath, cors(), router);

  const server = app.listen(port, host, () => {
    console.log(`Server is running at http://${host}:${port}${basePath}`);
  });

  return server;
}