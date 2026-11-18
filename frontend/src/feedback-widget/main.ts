/** The bundle's entry: a classic, deferred script on the content host. */
import { boot } from './boot';
import { createPlatform } from './platform';

boot(createPlatform());
