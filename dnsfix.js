const dns = require("dns"), orig = dns.lookup;
dns.lookup = function (h, o, cb) {
  if (typeof o === "function") { cb = o; o = {}; }
  if (h === "api.vercel.com") return o && o.all ? cb(null, [{ address: "76.76.21.21", family: 4 }]) : cb(null, "76.76.21.21", 4);
  return orig.call(dns, h, o, cb);
};
