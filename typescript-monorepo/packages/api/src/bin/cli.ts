#!/usr/bin/env node

import yargs from 'yargs';

import { startServer } from '../server';

  
const argv = yargs(process.argv.slice(2))
    .option("port", {
        type: "number",
    })
    .option("host", {
        type: "string",
    })
    .option("base-path", {
        type: "string",
    })
    .help()
    .parseSync()

startServer(argv);