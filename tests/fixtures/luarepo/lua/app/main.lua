local net = require "app.net"
local pack = require('app/framing').pack
local json = require('dkjson')

net.send(pack(1))
net.count(2)

local function run(conn)
  conn.open()
  net:flush({})
  return json.encode({})
end

run()
