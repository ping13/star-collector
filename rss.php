<?php
// For production (no debug output):
error_reporting(0);

// For debugging (uncomment the next line):
// error_reporting(E_ALL);

// Debug function
function debug($message, $data = null) {
    if (error_reporting() !== 0) {
        echo "DEBUG: $message\n";
        if ($data !== null) {
            echo "Data: " . print_r($data, true) . "\n";
        }
        echo "\n";
    }
}

// Konfiguration
$config_file = 'mastodon_config.php';

// Funktion zum Einlesen der Konfiguration
function load_config($config_file) {
    debug("Loading configuration from $config_file");
    if (!file_exists($config_file)) {
        debug("Configuration file not found");
        die("Konfigurationsdatei nicht gefunden. Bitte erstellen Sie eine Datei namens '$config_file' mit dem Inhalt: 
<?php
\$access_token = 'IHR_ACCESS_TOKEN';
\$mastodon_instance = 'https://ihre_instanz.social';
\$mastodon_username = 'ihr_benutzername';
\$feed_item_limit = 5;
?>");
    }
    require $config_file;
    if (!isset($access_token) || empty($access_token)) {
        debug("Access token not found or empty");
        die("Access Token nicht gefunden oder leer in der Konfigurationsdatei.");
    }
    if (!isset($mastodon_instance) || empty($mastodon_instance)) {
        debug("Mastodon instance not found or empty");
        die("Mastodon-Instanz nicht gefunden oder leer in der Konfigurationsdatei.");
    }
    if (!isset($mastodon_username) || empty($mastodon_username)) {
        debug("Mastodon username not found or empty");
        die("Mastodon-Benutzername nicht gefunden oder leer in der Konfigurationsdatei.");
    }
    if (!isset($feed_item_limit) || !is_numeric($feed_item_limit) || $feed_item_limit <= 0) {
        debug("Feed item limit not found or invalid, setting default to 5");
        $feed_item_limit = 5;
    }
    debug("Configuration loaded successfully");
    return array(
        'access_token' => $access_token, 
        'mastodon_instance' => $mastodon_instance,
        'mastodon_username' => $mastodon_username,
        'feed_item_limit' => $feed_item_limit
    );
}

// Konfiguration aus der Datei laden
$config = load_config($config_file);
$access_token = $config['access_token'];
$mastodon_instance = $config['mastodon_instance'];
$mastodon_username = $config['mastodon_username'];
$feed_item_limit = $config['feed_item_limit'];

debug("Mastodon instance: $mastodon_instance");
debug("Mastodon username: $mastodon_username");
debug("Feed item limit: $feed_item_limit");

// Funktion zum Abrufen von Daten von der Mastodon API
function fetch_mastodon_data($endpoint) {
    global $mastodon_instance, $access_token;
    $url = $mastodon_instance . $endpoint;
    debug("Fetching data from: $url");
    $ch = curl_init($url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_HTTPHEADER, [
        'Authorization: Bearer ' . $access_token
    ]);
    $response = curl_exec($ch);
    if ($response === false) {
        $error = curl_error($ch);
        $errno = curl_errno($ch);
        debug("cURL error ($errno): $error");
        curl_close($ch);
        return null;
    }
    $http_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    debug("HTTP response code: $http_code");
    curl_close($ch);
    $data = json_decode($response, true);
    if (json_last_error() !== JSON_ERROR_NONE) {
        debug("JSON decode error: " . json_last_error_msg());
        return null;
    }
    debug("Fetched data", ['count' => is_array($data) ? count($data) : 'Not an array']);
    return $data;
}

// Funktion zum Erstellen eines RSS-Items
function create_rss_item($status) {
    debug("Creating RSS item for status ID: " . $status['id']);
    $item = new SimpleXMLElement('<item></item>');
    $item->addChild('title', htmlspecialchars('@' . $status['account']['username'] . ': ' . mb_substr(strip_tags($status['content']), 0, 100) . '...'));
    $item->addChild('link', $status['url']);
    $item->addChild('guid', $status['id']);
    $item->addChild('pubDate', date(DATE_RSS, strtotime($status['created_at'])));
    
    // Erstellen einer detaillierten Beschreibung mit eingebetteten Medien
    $description = htmlspecialchars($status['content']);
    if (!empty($status['media_attachments'])) {
        $description .= "\n\n<h3>Anhänge:</h3>\n";
        foreach ($status['media_attachments'] as $media) {
            switch ($media['type']) {
                case 'image':
                    $description .= "<p><img src='" . htmlspecialchars($media['url']) . "' alt='" . htmlspecialchars($media['description']) . "' style='max-width:100%;'/></p>\n";
                    break;
                case 'video':
                    $description .= "<p><video src='" . htmlspecialchars($media['url']) . "' controls style='max-width:100%;'>Ihr Browser unterstützt das Video-Tag nicht.</video></p>\n";
                    break;
                case 'gifv':
                    $description .= "<p><img src='" . htmlspecialchars($media['url']) . "' alt='" . htmlspecialchars($media['description']) . "' style='max-width:100%;'/></p>\n";
                    break;
                default:
                    $description .= "<p>Anhang: <a href='" . htmlspecialchars($media['url']) . "'>" . htmlspecialchars($media['type']) . "</a></p>\n";
            }
        }
    }
    
    $descriptionNode = $item->addChild('description');
    $descriptionNode[0] = '<![CDATA[' . $description . ']]>';
    
    // Anhänge als separate Elemente hinzufügen
    foreach ($status['media_attachments'] as $media) {
        $enclosure = $item->addChild('enclosure');
        $enclosure->addAttribute('url', $media['url']);
        $enclosure->addAttribute('type', $media['type'] === 'image' ? 'image/jpeg' : 'video/mp4');
        // Fügen Sie die Größe hinzu, wenn verfügbar
        if (isset($media['meta']['original']['size'])) {
            $enclosure->addAttribute('length', $media['meta']['original']['size']);
        }
    }
    
    debug("RSS item created successfully");
    return $item;
}

// Hauptfunktion zum Erstellen des RSS-Feeds
function generate_rss_feed() {
    global $mastodon_username, $feed_item_limit, $mastodon_instance;
    debug("Starting RSS feed generation for user: $mastodon_username with limit: $feed_item_limit");
    $favorites = fetch_mastodon_data("/api/v1/favourites");
    if ($favorites === null) {
        debug("Failed to fetch favorites");
        return null;
    }
    $bookmarks = fetch_mastodon_data("/api/v1/bookmarks");
    if ($bookmarks === null) {
        debug("Failed to fetch bookmarks");
        return null;
    }

    $all_statuses = array_merge($favorites, $bookmarks);
    debug("Total statuses fetched", ['count' => count($all_statuses)]);

    // Entfernen von Duplikaten basierend auf der Status-ID
    $unique_statuses = [];
    foreach ($all_statuses as $status) {
        $unique_statuses[$status['id']] = $status;
    }
    debug("Unique statuses", ['count' => count($unique_statuses)]);

    // Sortieren nach Datum (neueste zuerst)
    usort($unique_statuses, function($a, $b) {
        return strtotime($b['created_at']) - strtotime($a['created_at']);
    });

    // Limit the number of items
    $unique_statuses = array_slice($unique_statuses, 0, $feed_item_limit);
    debug("Limited unique statuses", ['count' => count($unique_statuses)]);

    // RSS-Feed erstellen
    debug("Creating RSS feed XML");
    $rss = new SimpleXMLElement('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"></rss>');
    $channel = $rss->addChild('channel');
    $channel->addChild('title', "Mastodon Favoriten und Lesezeichen von @$mastodon_username");
    $channel->addChild('link', "$mastodon_instance/@$mastodon_username");
    $channel->addChild('description', "Ein Feed der Mastodon Favoriten und Lesezeichen von @$mastodon_username");

    foreach ($unique_statuses as $status) {
        $item = create_rss_item($status);
        $dom = dom_import_simplexml($channel);
        $item_dom = dom_import_simplexml($item);
        $dom->appendChild($dom->ownerDocument->importNode($item_dom, true));
    }

    debug("RSS feed generation complete");
    return $rss->asXML();
}

// RSS-Feed generieren und ausgeben
debug("Script execution started");
$feed_content = generate_rss_feed();

if ($feed_content === null) {
    debug("Feed generation failed");
    die("Es ist ein Fehler bei der Generierung des Feeds aufgetreten. Bitte überprüfen Sie die Debug-Ausgabe für weitere Details.");
} elseif (empty($feed_content)) {
    debug("Feed content is empty");
    die("Der generierte Feed ist leer. Möglicherweise gibt es keine Favoriten oder Lesezeichen.");
} else {
    debug("Outputting feed content");
    header('Content-Type: application/rss+xml; charset=utf-8');
    echo $feed_content;
}

debug("Script execution completed");
?>
