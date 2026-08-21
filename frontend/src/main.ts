import "element-plus/dist/index.css";

import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";

import ElementPlus from "element-plus";
import { createApp } from "vue";

import App from "./App.vue";
import "./styles/tokens.css";
import "./styles/base.css";

createApp(App).use(ElementPlus).mount("#app");
