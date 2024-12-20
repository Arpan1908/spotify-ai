import os
import google.generativeai as genai
import spotipy
import streamlit as st
import json
import requests
from dotenv import load_dotenv

load_dotenv()

# Configure Gemini API key
if "GEMINI_API_KEY" not in os.environ:
    st.error("Please set GEMINI_API_KEY in your environment variables")
    st.stop()

genai.configure(api_key=os.environ["GEMINI_API_KEY"])

def initialize_session_state():
    """Initialize session state variables"""
    if "spotify_client" not in st.session_state:
        st.session_state.spotify_client = None

@st.cache_data
def get_spotify_client(_authorization_code):
    """Get Spotify client using authorization code"""
    try:
        response = requests.post(
            "https://accounts.spotify.com/api/token",
            data={
                "grant_type": "authorization_code",
                "code": _authorization_code,
                "redirect_uri": "http://localhost:8501",
                "client_id": os.environ.get("SPOTIFY_CLIENT_ID"),
                "client_secret": os.environ.get("SPOTIFY_CLIENT_SECRET"),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        
        response.raise_for_status()  # Raise exception for bad status codes
        access_token = response.json()["access_token"]
        return spotipy.Spotify(auth=access_token)
    
    except requests.exceptions.RequestException as e:
        st.error(f"Failed to get access token: {str(e)}")
        return None
    except KeyError as e:
        st.error(f"Unexpected response format: {str(e)}")
        return None

def login_spotify():
    """Handle Spotify OAuth login flow"""
    if not os.environ.get("SPOTIFY_CLIENT_ID") or not os.environ.get("SPOTIFY_CLIENT_SECRET"):
        st.error("Please set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in your environment variables")
        st.stop()

    redirect_uri = "http://localhost:8501"
    auth_url = (
        "https://accounts.spotify.com/authorize"
        f"?client_id={os.environ['SPOTIFY_CLIENT_ID']}"
        "&response_type=code"
        f"&redirect_uri={redirect_uri}"
        "&scope=playlist-modify-public playlist-modify-private"  # Added public scope
    )

    query_params = st.experimental_get_query_params()
    if "code" in query_params:
        return query_params["code"][0]

    st.markdown(
        f"Please [log in to Spotify]({auth_url}) to continue",
        unsafe_allow_html=True,
    )
    return None

def extract_json_from_text(text):
    """Extract JSON from text that might contain additional content"""
    try:
        # First try to parse the entire text as JSON
        return json.loads(text)
    except json.JSONDecodeError:
        # If that fails, try to find JSON-like content
        try:
            # Look for content between curly braces
            start = text.find('{')
            end = text.rfind('}') + 1
            if start != -1 and end != 0:
                json_str = text[start:end]
                return json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            return None
    return None

def generate_playlist_data(prompt, song_count):
    """Generate playlist data using Gemini AI"""
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt_template = (
            "You are a music expert creating a Spotify playlist. "
            f"Create a playlist with exactly {song_count} songs that fits this description: '{prompt}'. "
            "Respond with ONLY valid JSON in this exact format:\n"
            "{\n"
            '    "playlist_name": "A creative name for the playlist",\n'
            '    "playlist_description": "A brief description of the playlist theme",\n'
            '    "songs": [\n'
            '        {"songname": "Actual Song Title", "artists": ["Real Artist Name"]},\n'
            '        ...\n'
            '    ]\n'
            "}\n\n"
            "Important:\n"
            "1. Use real, existing songs and artists\n"
            "2. Return ONLY the JSON, no other text\n"
            "3. Ensure the JSON is properly formatted\n"
            f"4. Include exactly {song_count} songs"
        )
        
        response = model.generate_content(prompt_template)
        
        # For debugging
        st.write("Raw AI response:", response.text)
        
        # Try to extract and parse JSON from the response
        playlist_data = extract_json_from_text(response.text)
        if not playlist_data:
            st.error("Could not parse AI response as JSON")
            return None
            
        # Validate the required fields
        required_fields = ["playlist_name", "playlist_description", "songs"]
        if not all(field in playlist_data for field in required_fields):
            st.error("AI response missing required fields")
            return None
            
        # Validate songs structure
        if not isinstance(playlist_data["songs"], list):
            st.error("Songs field is not a list")
            return None
            
        for song in playlist_data["songs"]:
            if not isinstance(song, dict) or "songname" not in song or "artists" not in song:
                st.error("Invalid song format in AI response")
                return None
                
        return playlist_data
        
    except Exception as e:
        st.error(f"Error generating playlist: {str(e)}")
        return None

def create_spotify_playlist(spotify_client, playlist_data):
    """Create Spotify playlist and add songs"""
    try:
        # Get user ID
        user_id = spotify_client.me()["id"]
        
        # Create playlist
        playlist_name = "AI - " + playlist_data["playlist_name"]
        playlist_description = playlist_data.get("playlist_description", "AI-generated playlist")
        playlist = spotify_client.user_playlist_create(
            user_id, 
            playlist_name, 
            public=False, 
            description=playlist_description
        )
        
        # Find and add songs
        song_uris = []
        not_found = []
        
        with st.spinner("Finding songs on Spotify..."):
            for song in playlist_data["songs"]:
                search_query = f"{song['songname']} {' '.join(song['artists'])}"
                results = spotify_client.search(q=search_query, limit=1, type="track")
                
                if results["tracks"]["items"]:
                    song_uris.append(results["tracks"]["items"][0]["uri"])
                else:
                    not_found.append(f"{song['songname']} by {', '.join(song['artists'])}")
        
        if song_uris:
            spotify_client.playlist_add_items(playlist["id"], song_uris)
            st.success(f"Created playlist with {len(song_uris)} songs!")
            st.markdown(
                f"[Open playlist on Spotify]({playlist['external_urls']['spotify']})",
                unsafe_allow_html=True
            )
            
            if not_found:
                st.warning("Some songs couldn't be found:" + "\n- " + "\n- ".join(not_found))
        else:
            st.error("No songs could be found on Spotify. Please try again with different input.")
            
    except Exception as e:
        st.error(f"Error creating playlist: {str(e)}")

def main():
    st.title("AI Playlist Generator")
    initialize_session_state()
    
    # Handle Spotify authentication
    if not st.session_state.spotify_client:
        authorization_code = login_spotify()
        if authorization_code:
            st.session_state.spotify_client = get_spotify_client(authorization_code)
        
    if not st.session_state.spotify_client:
        return

    # Playlist generation form
    with st.form("playlist_generation"):
        prompt = st.text_area(
            "Describe the playlist you want...",
            placeholder="Example: Upbeat workout music with a mix of rock and hip-hop"
        )
        song_count = st.slider("Number of songs", 5, 30, 10)
        submitted = st.form_submit_button("Generate Playlist")

    if submitted and prompt:
        with st.spinner("Generating playlist suggestions..."):
            playlist_data = generate_playlist_data(prompt, song_count)
            if playlist_data:
                create_spotify_playlist(st.session_state.spotify_client, playlist_data)
    
if __name__ == "__main__":
    main()