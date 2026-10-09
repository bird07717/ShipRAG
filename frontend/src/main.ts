import "element-plus/dist/index.css";

import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";

import { createApp } from "vue";

import App from "./App.vue";
import { installElementPlus } from "./elementPlus";
import "./styles/tokens.css";
import "./styles/base.css";

const app = createApp(App);
installElementPlus(app);
app.mount("#app");
